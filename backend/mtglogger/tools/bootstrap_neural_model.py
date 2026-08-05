"""Download and verify MTGLogger's pinned neural embedding model."""

from ..services.neural import download_official_model


def main() -> None:
    print(download_official_model())


if __name__ == "__main__":
    main()
