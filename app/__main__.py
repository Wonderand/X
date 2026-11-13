import uvicorn
from app.config import Settings


def main():
    settings = Settings.from_env()
    uvicorn.run('app.main:create_app', factory=True, host=settings.bind_host, port=settings.bind_port, proxy_headers=False)


if __name__ == '__main__':
    main()
