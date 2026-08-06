"""Compact neural image embeddings and an exact in-memory cosine index."""

from __future__ import annotations

import hashlib
import json
import logging
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CardNeuralEmbedding, CardReference

logger = logging.getLogger(__name__)

MODEL_URL = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/"
    "paddle3.0b2/PP-ShiTuV2_rec_infer.tar"
)
MODEL_SHA256 = "24fbbc003ee5ba62e8821f5b580ae62467aaba4f8a6fa362a19de8c9adbd304b"
MODEL_SUBDIR = "PP-ShiTuV2_rec_infer"


def _model_files(model_root: Path) -> tuple[Path, Path]:
    directory = model_root / MODEL_SUBDIR
    return directory / "inference.pdmodel", directory / "inference.pdiparams"


def model_is_ready(model_root: Path | None = None) -> bool:
    model, params = _model_files(model_root or get_settings().neural_model_dir)
    return model.is_file() and params.is_file()


def download_official_model(model_root: Path | None = None) -> Path:
    """Install the pinned official model after checksum and archive-path validation."""
    root = model_root or get_settings().neural_model_dir
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "PP-ShiTuV2_rec_infer.tar"
    with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        digest = hashlib.sha256()
        with archive.open("wb") as output:
            for chunk in response.iter_bytes():
                digest.update(chunk)
                output.write(chunk)
    if digest.hexdigest() != MODEL_SHA256:
        archive.unlink(missing_ok=True)
        raise RuntimeError("Downloaded neural model failed its SHA-256 verification")
    with tarfile.open(archive) as bundle:
        root_resolved = root.resolve()
        for member in bundle.getmembers():
            destination = (root / member.name).resolve()
            if root_resolved not in destination.parents and destination != root_resolved:
                raise RuntimeError("Neural model archive contains an unsafe path")
        bundle.extractall(root, filter="data")
    archive.unlink(missing_ok=True)
    if not model_is_ready(root):
        raise RuntimeError("Neural model archive did not contain inference weights")
    return root / MODEL_SUBDIR


class NeuralEmbedder:
    """Thread-safe lazy Paddle predictor for PP-ShiTuV2 retrieval features."""

    def __init__(self, model_root: Path | None = None):
        self.model_root = model_root or get_settings().neural_model_dir
        self._predictor = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return model_is_ready(self.model_root)

    def warm(self) -> None:
        """Load the predictor without charging initialization to the first scan."""
        self._load()

    def _load(self):
        if self._predictor is not None:
            return self._predictor
        if not self.available:
            raise RuntimeError(f"Neural model is not installed in {self.model_root}")
        try:
            import paddle.inference as paddle_infer
        except ImportError as exc:
            raise RuntimeError("Paddle inference runtime is not installed") from exc
        model, params = _model_files(self.model_root)
        config = paddle_infer.Config(str(model), str(params))
        config.disable_gpu()
        config.set_cpu_math_library_num_threads(4)
        config.enable_memory_optim()
        config.switch_ir_optim(True)
        config.disable_glog_info()
        self._predictor = paddle_infer.create_predictor(config)
        return self._predictor

    @staticmethod
    def preprocess(image: np.ndarray) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("Cannot embed an empty image")
        resized = cv2.resize(image, (224, 224), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
            [0.229, 0.224, 0.225], dtype=np.float32
        )
        return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...])

    def embed(self, image: np.ndarray) -> np.ndarray:
        return self.embed_many([image])[0]

    def embed_many(self, images: list[np.ndarray]) -> np.ndarray:
        if not images:
            return np.empty((0, 0), dtype=np.float32)
        predictor = self._load()
        tensor = np.concatenate([self.preprocess(image) for image in images], axis=0)
        with self._lock:
            input_handle = predictor.get_input_handle(predictor.get_input_names()[0])
            input_handle.reshape(tensor.shape)
            input_handle.copy_from_cpu(tensor)
            predictor.run()
            output = predictor.get_output_handle(predictor.get_output_names()[0]).copy_to_cpu()
        vectors = np.asarray(output, dtype=np.float32).reshape(len(images), -1)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if not np.all(np.isfinite(norms)) or np.any(norms <= 0):
            raise RuntimeError("Neural model returned an invalid feature vector")
        return vectors / norms

    def embed_path(self, path: Path) -> np.ndarray:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not decode image {path}")
        return self.embed(image)


def encode_vector(vector: np.ndarray) -> bytes:
    normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
    return normalized.astype(np.float16).tobytes()


def decode_vector(payload: bytes, dimensions: int) -> np.ndarray:
    vector = np.frombuffer(payload, dtype=np.float16, count=dimensions).astype(np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


@dataclass(frozen=True)
class NeuralMatch:
    reference: CardReference
    similarity: float
    source_kind: str


class NeuralIndex:
    """Snapshot of normalized embeddings; dot product is exact cosine retrieval."""

    def __init__(self, db: Session, model_version: str | None = None):
        version = model_version or get_settings().neural_model_version
        rows = db.execute(
            select(CardNeuralEmbedding, CardReference)
            .join(CardReference, CardReference.scryfall_id == CardNeuralEmbedding.scryfall_id)
            .where(CardNeuralEmbedding.model_version == version)
        ).all()
        self.references = [row[1] for row in rows]
        self.source_kinds = [row[0].source_kind for row in rows]
        self.source_ids = [row[0].source_id for row in rows]
        self.indices_by_id: dict[str, list[int]] = {}
        self.indices_by_name: dict[str, list[int]] = {}
        for index, reference in enumerate(self.references):
            self.indices_by_id.setdefault(reference.scryfall_id, []).append(index)
            self.indices_by_name.setdefault(reference.name, []).append(index)
        self.adapter = MetricAdapter.load_active()
        vectors = [
            self.adapter.apply(decode_vector(row[0].vector, row[0].dimensions)) for row in rows
        ]
        self.matrix = np.stack(vectors) if vectors else np.empty((0, 0), dtype=np.float32)

    def search(
        self,
        query: np.ndarray,
        limit: int = 10,
        *,
        allowed_ids: set[str] | None = None,
        allowed_names: set[str] | None = None,
        ignored_source_ids: set[str] | None = None,
    ) -> list[NeuralMatch]:
        vector = np.asarray(query, dtype=np.float32).reshape(-1)
        if not len(self.references) or self.matrix.shape[1] != vector.shape[0]:
            return []
        vector = self.adapter.apply(vector)
        # Apply identity constraints before the matrix multiplication. Scanner
        # OCR commonly narrows 100k embeddings to a few dozen printings; taking
        # a full-gallery dot product and discarding 99.9% afterward turned this
        # supporting signal into several seconds of avoidable latency.
        if allowed_ids:
            eligible_indices = np.fromiter(
                (
                    index
                    for scryfall_id in allowed_ids
                    for index in self.indices_by_id.get(scryfall_id, ())
                ),
                dtype=np.int64,
            )
        elif allowed_names:
            eligible_indices = np.fromiter(
                (
                    index
                    for name in allowed_names
                    for index in self.indices_by_name.get(name, ())
                ),
                dtype=np.int64,
            )
        else:
            eligible_indices = np.arange(len(self.references), dtype=np.int64)
        if allowed_ids and allowed_names and len(eligible_indices):
            eligible_indices = np.asarray(
                [
                    index
                    for index in eligible_indices
                    if self.references[index].name in allowed_names
                ],
                dtype=np.int64,
            )
        if ignored_source_ids and len(eligible_indices):
            eligible_indices = np.asarray(
                [
                    index
                    for index in eligible_indices
                    if self.source_ids[index] not in ignored_source_ids
                ],
                dtype=np.int64,
            )
        count = min(max(limit * 4, 0), len(eligible_indices))
        if count == 0:
            return []
        eligible_scores = self.matrix[eligible_indices] @ vector
        selected = np.argpartition(eligible_scores, -count)[-count:]
        selected = selected[np.argsort(eligible_scores[selected])[::-1]]
        matches: list[NeuralMatch] = []
        seen: set[str] = set()
        for selected_index in selected:
            index = int(eligible_indices[selected_index])
            reference = self.references[index]
            if reference.scryfall_id in seen:
                continue
            matches.append(
                NeuralMatch(
                    reference,
                    float(eligible_scores[selected_index]),
                    self.source_kinds[index],
                )
            )
            seen.add(reference.scryfall_id)
            if len(matches) >= limit:
                break
        return matches


@dataclass(frozen=True)
class MetricAdapter:
    """Tiny learned diagonal metric; adds effectively zero scanner latency."""

    scale: np.ndarray | None = None
    version: str = "identity"

    def apply(self, vector: np.ndarray) -> np.ndarray:
        value = np.asarray(vector, dtype=np.float32).reshape(-1)
        if self.scale is not None and self.scale.shape == value.shape:
            value = value * self.scale
        norm = float(np.linalg.norm(value))
        return value / max(norm, 1e-12)

    @classmethod
    def load_active(cls) -> "MetricAdapter":
        root = get_settings().neural_index_dir
        manifest = root / "active-adapter.json"
        if not manifest.is_file():
            return cls()
        try:
            metadata = json.loads(manifest.read_text())
            weights = root / metadata["weights"]
            scale = np.load(weights, allow_pickle=False)["scale"].astype(np.float32)
            return cls(scale=scale, version=str(metadata["version"]))
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            logger.exception("Could not load active neural metric adapter; using identity")
            return cls()


class NeuralRetriever:
    """Process-wide lazy embedding index used by the scanner hot path."""

    _index: NeuralIndex | None = None
    _index_lock = threading.Lock()
    _refreshing = False

    def __init__(self):
        self.embedder = _default_embedder

    @classmethod
    def invalidate(cls) -> None:
        with cls._index_lock:
            cls._index = None

    @classmethod
    def warm(cls) -> int:
        index = cls._get_index()
        return len(index.references)

    def warm_model(self) -> None:
        if self.available:
            self.embedder.warm()

    @classmethod
    def refresh_in_background(cls) -> None:
        """Build a fresh snapshot without blocking searches using the old one."""
        with cls._index_lock:
            if cls._refreshing:
                return
            cls._refreshing = True

        def refresh() -> None:
            try:
                from ..database import SessionLocal

                with SessionLocal() as db:
                    replacement = NeuralIndex(db)
                with cls._index_lock:
                    cls._index = replacement
            except Exception:
                logger.exception("Could not refresh neural retrieval gallery")
            finally:
                with cls._index_lock:
                    cls._refreshing = False

        threading.Thread(target=refresh, name="neural-index-refresh", daemon=True).start()

    @classmethod
    def _get_index(cls) -> NeuralIndex:
        with cls._index_lock:
            if cls._index is None:
                from ..database import SessionLocal

                with SessionLocal() as db:
                    cls._index = NeuralIndex(db)
            return cls._index

    @property
    def available(self) -> bool:
        return get_settings().neural_enabled and self.embedder.available

    def embed(self, image: np.ndarray) -> np.ndarray | None:
        if not self.available:
            return None
        try:
            return self.embedder.embed(image)
        except Exception:
            logger.exception("Neural embedding failed; continuing with hybrid recognition")
            return None

    def search_vector(
        self,
        vector: np.ndarray | None,
        limit: int = 10,
        *,
        allowed_ids: set[str] | None = None,
        allowed_names: set[str] | None = None,
        ignored_source_ids: set[str] | None = None,
    ) -> list[NeuralMatch]:
        if vector is None:
            return []
        try:
            return self._get_index().search(
                vector,
                limit=limit,
                allowed_ids=allowed_ids,
                allowed_names=allowed_names,
                ignored_source_ids=ignored_source_ids,
            )
        except Exception:
            logger.exception("Neural retrieval failed; continuing with hybrid recognition")
            return []

    def search(self, image: np.ndarray, limit: int = 10) -> list[NeuralMatch]:
        return self.search_vector(self.embed(image), limit=limit)


def store_embedding(
    db: Session,
    *,
    scryfall_id: str,
    source_kind: str,
    source_id: str,
    vector: np.ndarray,
    model_version: str | None = None,
) -> CardNeuralEmbedding:
    version = model_version or get_settings().neural_model_version
    existing = db.scalar(
        select(CardNeuralEmbedding).where(
            CardNeuralEmbedding.model_version == version,
            CardNeuralEmbedding.source_kind == source_kind,
            CardNeuralEmbedding.source_id == source_id,
        )
    )
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    row = existing or CardNeuralEmbedding(
        scryfall_id=scryfall_id,
        model_version=version,
        source_kind=source_kind,
        source_id=source_id,
        dimensions=len(values),
        vector=b"",
    )
    row.scryfall_id = scryfall_id
    row.dimensions = len(values)
    row.vector = encode_vector(values)
    if existing is None:
        db.add(row)
    return row


_default_embedder = NeuralEmbedder()


def embed_and_store(
    db: Session,
    image: np.ndarray,
    *,
    scryfall_id: str,
    source_kind: str,
    source_id: str,
) -> CardNeuralEmbedding | None:
    """Embed a known image when neural support is installed; safely no-op otherwise."""
    settings = get_settings()
    if not settings.neural_enabled or not _default_embedder.available:
        return None
    return store_embedding(
        db,
        scryfall_id=scryfall_id,
        source_kind=source_kind,
        source_id=source_id,
        vector=_default_embedder.embed(image),
    )
