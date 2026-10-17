"""Query log for AdGuardHome."""

from cli_anything.adguardhome.utils.adguardhome_backend import AdGuardHomeClient

MS_PER_DAY = 24 * 60 * 60 * 1000


def get_log(client: AdGuardHomeClient, limit: int = 100, offset: int = 0) -> dict:
    return client.get("/querylog", params={"limit": limit, "offset": offset})


def get_log_config(client: AdGuardHomeClient) -> dict:
    return client.get("/querylog/config")


def set_log_config(client: AdGuardHomeClient, enabled: bool | None = None,
                   interval_days: int | None = None) -> dict:
    """Update the query log settings and return the resulting config.

    The update endpoint replaces the whole config, so the current one is read
    first and only the requested fields are changed. The API expresses the
    retention interval in milliseconds.
    """
    config = get_log_config(client)
    if enabled is not None:
        config["enabled"] = enabled
    if interval_days is not None:
        config["interval"] = interval_days * MS_PER_DAY
    client.put("/querylog/config/update", config)
    return get_log_config(client)


def clear_log(client: AdGuardHomeClient) -> dict:
    return client.post("/querylog_clear")
