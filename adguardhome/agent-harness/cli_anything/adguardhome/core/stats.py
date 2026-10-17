"""Statistics for AdGuardHome."""

from cli_anything.adguardhome.utils.adguardhome_backend import AdGuardHomeClient

MS_PER_DAY = 24 * 60 * 60 * 1000


def get_stats(client: AdGuardHomeClient) -> dict:
    return client.get("/stats")


def reset_stats(client: AdGuardHomeClient) -> dict:
    return client.post("/stats_reset")


def get_stats_config(client: AdGuardHomeClient) -> dict:
    return client.get("/stats/config")


def set_stats_config(client: AdGuardHomeClient, interval_days: int) -> dict:
    """Update the statistics retention and return the resulting config.

    The update endpoint replaces the whole config, so the current one is read
    first and only the interval is changed. The API expresses it in milliseconds.
    """
    config = get_stats_config(client)
    config["interval"] = interval_days * MS_PER_DAY
    client.put("/stats/config/update", config)
    return get_stats_config(client)
