from fastapi import APIRouter, Depends

from auth.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/me")
def get_me(user: AuthenticatedUser = Depends(get_current_user)):
    return {
        "authenticated": True,
        "user_id": user.id,
        "email": user.email,
    }