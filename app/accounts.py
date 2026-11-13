from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.db import event
from app.errors import DomainError


def update_account(db, values):
    try:
        ZoneInfo(values['timezone'])
        limit, generation = int(values['daily_limit']), int(values['generation_limit'])
        if not (1 <= limit <= 20 and 0 <= generation <= 100):
            raise ValueError()
        if not all(str(values.get(key, '')).strip() for key in ('name', 'topics', 'audience', 'tone')):
            raise ValueError()
    except (KeyError, ValueError, ZoneInfoNotFoundError):
        raise DomainError('请填写账号、读者、主题、语气、有效时区和数量限制') from None
    with db.transaction() as conn:
        conn.execute('UPDATE accounts SET name=?,topics=?,audience=?,tone=?,timezone=?,daily_limit=?,generation_limit=? WHERE id=1',
                     (values['name'][:100], values['topics'][:1000], values['audience'][:500], values['tone'][:500], values['timezone'], limit, generation))
        event(conn, 'account', 1, 'settings_updated', 'admin')
    return db.one('SELECT * FROM accounts WHERE id=1')
