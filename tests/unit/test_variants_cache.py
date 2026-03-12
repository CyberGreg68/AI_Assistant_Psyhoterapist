import time

from assistant_runtime.cache.variants_cache import VariantsCache


def test_variants_cache_expires_entries() -> None:
    cache = VariantsCache(ttl_seconds=1, max_entries=4)
    cache.set("key", "value")
    assert cache.get("key") == "value"
    time.sleep(1.1)
    assert cache.get("key") is None
