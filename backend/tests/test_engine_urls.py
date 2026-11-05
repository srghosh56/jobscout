from jobscout.db.engine import async_url, sync_url


def test_tls_option_is_translated_between_drivers() -> None:
    neon = "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require&channel_binding=require"
    a = async_url(neon)
    assert (
        a.startswith("postgresql+asyncpg://") and "ssl=require" in a and "channel_binding" not in a
    )
    s = sync_url(a)
    assert s.startswith("postgresql+psycopg://") and "sslmode=require" in s
    assert sync_url("postgresql+asyncpg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
