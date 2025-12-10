# from sqlalchemy.orm import Session
# from datetime import datetime
# from .models import CachedInterfaces

# def get_cached_interfaces(db: Session, device: str):
#     row = (
#         db.query(CachedInterfaces)
#         .filter(CachedInterfaces.device == device)
#         .one_or_none()
#     )
#     return row

# def save_cached_interfaces(db: Session, device: str, data):
#     row = get_cached_interfaces(db, device)

#     if row:
#         row.data = data
#         row.updated_at = datetime.utcnow()
#     else:
#         row = CachedInterfaces(
#             device=device,
#             data=data
#         )
#         db.add(row)

#     db.commit()
#     return row
