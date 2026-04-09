# test_token_store.py
from pathlib import Path
from src.token_store import TokenStore

HERE = Path(__file__).parent
store = TokenStore(HERE / "tokens.seed.json")

print("--- Flattened tokens ---")
for key, value in store.tokens().items():
    print(f"  {key}: {value}")

print("\n--- Round-trip test ---")
store.set("node.selected", "#ff0000")
store.save()

store2 = TokenStore(HERE / "tokens.seed.json")
assert store2.tokens()["node.selected"] == "#ff0000", "Round-trip failed"
print("  Round-trip passed")

store2.set("node.selected", "#7eb8f7")
store2.save()
print("  Restored original value")