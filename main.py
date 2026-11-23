from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from apscheduler.schedulers.background import BackgroundScheduler

from .database import Base, engine, get_db, SessionLocal
from .models import User, Device, Conversation, Membership, Message, utcnow
from .schemas import RegisterIn, LoginIn, ConversationIn, MessageIn
from .security import hash_password, verify_password, create_token, decode_token

Base.metadata.create_all(bind=engine)
scheduler = BackgroundScheduler(timezone="UTC")

def cleanup_expired():
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=5)
        # SQLite may return naive datetimes; compare via ISO-safe normalization.
        rows = db.query(Message).all()
        for msg in rows:
            created = msg.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                db.delete(msg)
        db.commit()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not scheduler.running:
        scheduler.add_job(cleanup_expired, "interval", hours=1, id="message_cleanup", replace_existing=True)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown()

app = FastAPI(title="ROTC Messaging API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    payload = decode_token(authorization[7:])
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(401, "Account unavailable")
    device = db.query(Device).filter_by(user_id=user.id, device_id=payload.get("device_id")).first()
    if not device:
        raise HTTPException(401, "This device session has been revoked")
    device.last_seen = utcnow()
    db.commit()
    return user

def require_owner(user: User):
    if user.role != "owner":
        raise HTTPException(403, "Owner access required")

def require_admin(user: User):
    if user.role not in ("owner", "admin"):
        raise HTTPException(403, "Administrator access required")

def is_member(db: Session, conversation_id: int, user_id: int) -> bool:
    return db.query(Membership).filter_by(conversation_id=conversation_id, user_id=user_id).first() is not None

@app.get("/")
def root():
    return {"message": "ROTC Messaging API is running"}

@app.post("/auth/register")
def register(data: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=data.username.lower()).first():
        raise HTTPException(409, "Username already exists")
    first = db.query(User).count() == 0
    user = User(
        username=data.username.lower(),
        display_name=data.display_name,
        password_hash=hash_password(data.password),
        role="owner" if first else "cadet"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role,
            "notice": "By using this system, you acknowledge that authorized administrators and the application owner can access conversations. Messages are scheduled for deletion after five days."}

@app.post("/auth/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=data.username.lower()).first()
    if not user or not verify_password(data.password, user.password_hash) or not user.active:
        raise HTTPException(401, "Incorrect username or password")
    device = db.query(Device).filter_by(user_id=user.id, device_id=data.device_id).first()
    if not device:
        devices = db.query(Device).filter_by(user_id=user.id).all()
        if len(devices) >= 2:
            raise HTTPException(403, "Two-device limit reached. Revoke an existing device before adding another.")
        device = Device(user_id=user.id, device_id=data.device_id)
        db.add(device)
    device.last_seen = utcnow()
    db.commit()
    return {"access_token": create_token(user.id, data.device_id), "token_type": "bearer",
            "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}}

@app.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}

@app.get("/users")
def users(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role}
            for u in db.query(User).filter(User.active == True).all()]

@app.post("/conversations")
def create_conversation(data: ConversationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    convo = Conversation(title=data.title, is_group=data.is_group, created_by=user.id)
    db.add(convo)
    db.flush()
    ids = set(data.member_ids + [user.id])
    for uid in ids:
        target = db.get(User, uid)
        if not target or not target.active:
            raise HTTPException(404, f"User {uid} not found")
        db.add(Membership(conversation_id=convo.id, user_id=uid))
    db.commit()
    return {"id": convo.id, "title": convo.title, "is_group": convo.is_group}

@app.get("/conversations")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    memberships = db.query(Membership).filter_by(user_id=user.id).all()
    result = []
    for m in memberships:
        c = db.get(Conversation, m.conversation_id)
        last = db.query(Message).filter_by(conversation_id=c.id).order_by(Message.created_at.desc()).first()
        result.append({"id": c.id, "title": c.title, "is_group": c.is_group,
                       "last_message": last.body if last else ""})
    return result

@app.get("/conversations/{conversation_id}/messages")
def get_messages(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not is_member(db, conversation_id, user.id):
        raise HTTPException(403, "You are not a member of this conversation")
    messages = db.query(Message).filter_by(conversation_id=conversation_id).order_by(Message.created_at.asc()).all()
    return [{"id": m.id, "sender_id": m.sender_id, "body": m.body,
             "created_at": m.created_at.isoformat()} for m in messages]

@app.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: int, data: MessageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not is_member(db, conversation_id, user.id):
        raise HTTPException(403, "You are not a member of this conversation")
    msg = Message(conversation_id=conversation_id, sender_id=user.id, body=data.body.strip())
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"id": msg.id, "sender_id": msg.sender_id, "body": msg.body, "created_at": msg.created_at.isoformat()}

@app.get("/owner/conversations")
def owner_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    return [{"id": c.id, "title": c.title, "is_group": c.is_group, "created_at": c.created_at.isoformat()}
            for c in db.query(Conversation).order_by(Conversation.created_at.desc()).all()]

@app.get("/owner/conversations/{conversation_id}/messages")
def owner_messages(conversation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_owner(user)
    if not db.get(Conversation, conversation_id):
        raise HTTPException(404, "Conversation not found")
    return [{"id": m.id, "sender_id": m.sender_id, "body": m.body, "created_at": m.created_at.isoformat()}
            for m in db.query(Message).filter_by(conversation_id=conversation_id).order_by(Message.created_at.asc()).all()]

@app.post("/admin/cleanup")
def manual_cleanup(user: User = Depends(current_user)):
    require_admin(user)
    cleanup_expired()
    return {"message": "Expired messages cleanup completed"}

@app.get("/admin/devices/{user_id}")
def list_devices(user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_admin(user)
    return [{"id": d.id, "device_id": d.device_id, "last_seen": d.last_seen.isoformat()}
            for d in db.query(Device).filter_by(user_id=user_id).all()]

@app.delete("/admin/devices/{device_record_id}")
def revoke_device(device_record_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_admin(user)
    device = db.get(Device, device_record_id)
    if not device:
        raise HTTPException(404, "Device not found")
    db.delete(device)
    db.commit()
    return {"message": "Device revoked"}
