from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import SealedProduct
from ..schemas import SealedCreate, SealedRead, SealedUpdate

router = APIRouter(prefix="/sealed", tags=["sealed"])


@router.get("", response_model=list[SealedRead])
def list_sealed(db: Session = Depends(get_db)):
    return list(db.scalars(select(SealedProduct).order_by(SealedProduct.date_added.desc())))


@router.post("", response_model=SealedRead, status_code=201)
def create_sealed(payload: SealedCreate, db: Session = Depends(get_db)):
    item = SealedProduct(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{item_id}", response_model=SealedRead)
def update_sealed(item_id: str, payload: SealedUpdate, db: Session = Depends(get_db)):
    item = db.get(SealedProduct, item_id)
    if not item:
        raise HTTPException(404, "Sealed product not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_sealed(item_id: str, db: Session = Depends(get_db)):
    item = db.get(SealedProduct, item_id)
    if not item:
        raise HTTPException(404, "Sealed product not found")
    db.delete(item)
    db.commit()
