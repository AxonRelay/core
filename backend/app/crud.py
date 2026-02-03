"""CRUD operations for database models."""
from sqlalchemy.orm import Session
from app import models


def get_user_by_email(db: Session, email: str):
    """Get user by email address."""
    return db.query(models.User).filter(models.User.email == email).first()


def get_user_by_oauth(db: Session, oauth_provider: str, oauth_id: str):
    """Get user by OAuth provider and ID."""
    return db.query(models.User).filter(
        models.User.oauth_provider == oauth_provider,
        models.User.oauth_id == oauth_id
    ).first()


def create_user(db: Session, email: str, name: str | None, oauth_provider: str, oauth_id: str):
    """Create a new user."""
    db_user = models.User(
        email=email,
        name=name,
        oauth_provider=oauth_provider,
        oauth_id=oauth_id
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def get_or_create_user(db: Session, email: str, name: str | None, oauth_provider: str, oauth_id: str):
    """Get existing user or create new one."""
    user = get_user_by_oauth(db, oauth_provider, oauth_id)
    if user:
        # Update name if changed
        if name and user.name != name:
            user.name = name
            db.commit()
            db.refresh(user)
        return user
    return create_user(db, email, name, oauth_provider, oauth_id)
