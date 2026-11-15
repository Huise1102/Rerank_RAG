import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from src.core.security import create_access_token
from src.db.models import AuthSession, User
from src.db.session import async_session_factory

HTML = """<!doctype html>
<html>
<body style="margin:0">
  <div id="tip" style="font:14px sans-serif;padding:8px">loading</div>
  <iframe id="f" style="width:100%;height:90vh;border:0"></iframe>
  <script>
    fetch('/api/v1/admin/documents/5/preview', { headers: { Authorization: 'Bearer %s' } })
      .then((r) => {
        document.getElementById('tip').textContent = 'status ' + r.status + ' type ' + r.headers.get('content-type')
        return r.blob()
      })
      .then((b) => {
        document.getElementById('tip').textContent += ' size ' + b.size + ' blobtype ' + b.type
        const u = URL.createObjectURL(new Blob([b], { type: 'application/pdf' }))
        document.getElementById('f').src = u
      })
      .catch((e) => { document.getElementById('tip').textContent = 'ERR ' + e })
  </script>
</body>
</html>
"""


async def main():
    jti = 'probe-' + uuid.uuid4().hex[:8]
    async with async_session_factory() as session:
        admin = (await session.execute(select(User).where(User.role == 'admin').limit(1))).scalar_one()
        session.add(
            AuthSession(
                jti=jti,
                user_id=admin.id,
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=20),
            )
        )
        await session.commit()
    token, _ = create_access_token(admin.id, jti)
    target = Path(__file__).resolve().parent.parent / 'frontend' / 'public' / '__pdf_test.html'
    target.write_text(HTML % token, encoding='utf-8')
    print('written', target)
    print('jti', jti)


asyncio.run(main())
