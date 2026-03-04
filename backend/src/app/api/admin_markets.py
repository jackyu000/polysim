from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth_deps import require_admin_user
from app.api.deps import get_db
from app.core.errors import MarketAlreadyResolved, MarketNotFound
from app.schemas.markets import ResolveMarketIn, ResolveMarketOut
from app.services.markets import resolve_market

router = APIRouter(prefix="/api/admin", tags=["admin-markets"])


@router.post("/markets/{market_id}/resolve", response_model=ResolveMarketOut)
def resolve_market_endpoint(
    market_id: str,
    payload: ResolveMarketIn,
    _admin=Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    try:
        result = resolve_market(db, market_id=market_id, resolved_outcome=payload.outcome)
        db.commit()
        return result
    except MarketNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except MarketAlreadyResolved as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        db.rollback()
        raise
