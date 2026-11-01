"""Versioned deterministic Admin CTO fuzz-case generation."""
import copy
import hashlib
import json


SCHEMA = "pump-admin-cto-fuzz-case-v1"
GENERATOR = "admin-cto-seed-stream-v1"
MODES = ("native", "spl", "token22")
ACCOUNT_TARGETS = ("global", "curve", "sharing", "pool", "quote_mint")
TOKEN_TARGETS = ("current_wallet_token", "creator_vault_token", "amm_vault_token")
WRONG_ACCOUNTS = (
    "global", "quote_mint", "bonding_curve", "current_creator",
    "pool_authority", "pool", "sharing_config", "pump_fees_event_authority",
)
BOUNDARIES = (0, 1, 2, 7, 8, 31, 32, 48, 49, 63, 64, 80, 81, 82, 83,
              108, 109, 114, 115, 123, 124, 125, 165, 166, 255, 256, 300,
              301, 890_879, 890_880, 890_881, 2**32, 2**63, 2**64 - 1)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class SeedStream:
    """SHA-256 counter stream whose output is independent of Python RNG details."""

    def __init__(self, seed: int):
        if type(seed) is not int or not 0 <= seed < 2**128:
            raise ValueError("seed must be an unsigned 128-bit integer")
        self.seed = seed
        self.counter = 0
        self.buffer = b""

    def take(self, count: int) -> bytes:
        while len(self.buffer) < count:
            block = self.seed.to_bytes(16, "big") + self.counter.to_bytes(8, "big")
            self.buffer += hashlib.sha256(block).digest()
            self.counter += 1
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def below(self, limit: int) -> int:
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be positive")
        width = max(1, (limit.bit_length() + 7) // 8)
        ceiling = 1 << (width * 8)
        cutoff = ceiling - ceiling % limit
        while True:
            value = int.from_bytes(self.take(width), "big")
            if value < cutoff:
                return value % limit

    def choice(self, values):
        if not values:
            raise ValueError("cannot choose from an empty sequence")
        return values[self.below(len(values))]

    def chance(self, numerator: int, denominator: int = 100) -> bool:
        return self.below(denominator) < numerator


def derive_case_seed(root_seed: int, index: int) -> int:
    if type(root_seed) is not int or not 0 <= root_seed < 2**128:
        raise ValueError("root seed must be an unsigned 128-bit integer")
    if type(index) is not int or not 0 <= index < 2**64:
        raise ValueError("case index must be an unsigned 64-bit integer")
    raw = root_seed.to_bytes(16, "big") + index.to_bytes(8, "big")
    return int.from_bytes(hashlib.sha256(b"pump-admin-cto-fuzz-v1" + raw).digest()[:16], "big")


def _account_mutation(stream: SeedStream, mode: str) -> dict:
    target = stream.choice(ACCOUNT_TARGETS + (() if mode == "native" else TOKEN_TARGETS))
    if target in TOKEN_TARGETS:
        action = stream.choice(("token_amount", "token_state", "truncate", "owner"))
        if action == "token_amount":
            return {"target": target, "action": action,
                    "value": stream.choice((0, 1, 99, 100, 101, 10**6, 2**64 - 1))}
        if action == "token_state":
            return {"target": target, "action": action, "value": stream.choice((0, 1, 2, 255))}
        if action == "truncate":
            return {"target": target, "action": action,
                    "value": stream.choice((0, 1, 63, 64, 71, 72, 108, 109, 164, 165))}
        return {"target": target, "action": action,
                "value": stream.choice(("system", "pump", "fees", "token", "token22"))}
    action = stream.choice(("truncate", "set_byte", "owner", "lamports"))
    if action == "truncate":
        limits = {
            "global": (0, 7, 8, 417, 418, 449, 450, 1045, 1054, 1086),
            "curve": (0, 7, 8, 48, 49, 80, 81, 82, 83, 114, 115, 123, 124, 125, 166),
            "sharing": (0, 7, 8, 10, 11, 42, 43, 75, 76, 79, 80, 113, 114, 1024),
            "pool": (0, 7, 8, 9, 10, 11, 42, 43, 74, 75, 210, 211, 243, 244, 301),
            "quote_mint": (0, 1, 43, 44, 45, 81, 82),
        }
        return {"target": target, "action": action, "value": stream.choice(limits[target])}
    if action == "set_byte":
        offsets = {"global": (8, 417, 418, 449, 1045, 1046, 1086),
                   "curve": (0, 8, 48, 49, 81, 82, 83, 115, 123, 124),
                   "sharing": (0, 8, 9, 10, 11, 43, 75, 76),
                   "pool": (0, 8, 9, 10, 11, 43, 75, 211, 243, 244),
                   "quote_mint": (0, 44, 45)}
        return {"target": target, "action": action, "offset": stream.choice(offsets[target]),
                "value": stream.choice((0, 1, 2, 127, 255))}
    if action == "owner":
        return {"target": target, "action": action,
                "value": stream.choice(("system", "pump", "fees", "token", "token22"))}
    return {"target": target, "action": action,
            "value": stream.choice((0, 1, 890_879, 890_880, 890_881, 2_039_280, 10**9))}


def _step(stream: SeedStream, index: int) -> dict:
    step = {"name": f"step-{index}", "op": "admin_cto"}
    if stream.chance(70):
        step["new"] = stream.below(4)
    elif stream.chance(50):
        step["new_none"] = True
    if stream.chance(55):
        step["holder"] = bool(stream.below(2))
    if stream.chance(55):
        step["fee"] = stream.choice((0, 1, 299, 300, 301, 10_000, 10_001, 2**64 - 1))
    if stream.chance(25):
        step["wrong"] = stream.choice(WRONG_ACCOUNTS)
    if stream.chance(20):
        step["readonly"] = [stream.below(28)]
    if stream.chance(15):
        step["replace_accounts"] = [{"index": stream.below(28),
                                      "creator": stream.below(4)}]
    if stream.chance(12):
        step["drop_accounts"] = stream.choice((1, 2, 3, 8, 16, 27))
    if stream.chance(20):
        kind = stream.choice(("truncate", "append", "holder_tag"))
        if kind == "truncate":
            step["instruction_mutation"] = {"action": kind,
                                             "value": stream.choice((0, 7, 8, 9, 10, 17, 18, 41, 42, 50))}
        elif kind == "append":
            step["instruction_mutation"] = {"action": kind,
                                             "value": stream.choice(("00", "ff", "ffff"))}
        else:
            step["instruction_mutation"] = {"action": kind,
                                             "value": stream.choice((2, 3, 255))}
    return step


def case_identity(case: dict) -> str:
    value = copy.deepcopy(case)
    value.pop("case_id", None)
    return sha(canonical(value))


def validate_case(case: dict) -> dict:
    if not isinstance(case, dict) or case.get("schema") != SCHEMA:
        raise ValueError("invalid case schema")
    if case.get("generator") != GENERATOR:
        raise ValueError("invalid generator version")
    for field in ("root_seed", "case_index", "case_seed"):
        if type(case.get(field)) is not int or case[field] < 0:
            raise ValueError(f"invalid {field}")
    if case["case_seed"] != derive_case_seed(case["root_seed"], case["case_index"]):
        raise ValueError("case seed does not match root seed and index")
    config = case.get("config")
    if not isinstance(config, dict) or config.get("mints") not in (["native"], ["spl"], ["token22"]):
        raise ValueError("invalid fixture config")
    mutations = case.get("initial_mutations")
    steps = case.get("steps")
    if not isinstance(mutations, list) or len(mutations) > 8:
        raise ValueError("invalid mutation list")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
        raise ValueError("invalid step list")
    if any(not isinstance(row, dict) or row.get("op") != "admin_cto" for row in steps):
        raise ValueError("invalid transaction step")
    expected = case_identity(case)
    if case.get("case_id") != expected:
        raise ValueError("case identity mismatch")
    return case


def generate_case(root_seed: int, index: int) -> dict:
    case_seed = derive_case_seed(root_seed, index)
    stream = SeedStream(case_seed)
    mode = stream.choice(MODES)
    config = {"mints": [mode], "shared": stream.chance(30),
              "complete": stream.chance(10), "holder_initial": stream.chance(20)}
    if mode == "native":
        config["native_vault_lamports"] = stream.choice((0, 1, 890_879, 890_880, 890_881, 10**6))
    else:
        config["spl_vault_tokens"] = stream.choice((0, 1, 99, 100, 101, 10**6))
        config["amm_tokens"] = stream.choice((0, 1, 100, 10**6))
        if stream.chance(20):
            config[stream.choice(("frozen_pump", "frozen_wallet", "frozen_amm"))] = True
    mutations = [_account_mutation(stream, mode) for _ in range(1 + stream.below(3))]
    steps = [_step(stream, i) for i in range(1, 2 + stream.below(3))]
    case = {"schema": SCHEMA, "generator": GENERATOR, "root_seed": root_seed,
            "case_index": index, "case_seed": case_seed, "config": config,
            "initial_mutations": mutations, "steps": steps}
    case["case_id"] = case_identity(case)
    return validate_case(case)


def generate_corpus(root_seed: int, count: int, start_index: int = 0) -> list[dict]:
    if type(count) is not int or not 1 <= count <= 10_000:
        raise ValueError("count must be between 1 and 10000")
    rows, identities = [], set()
    index = start_index
    while len(rows) < count:
        case = generate_case(root_seed, index)
        index += 1
        if case["case_id"] in identities:
            continue
        identities.add(case["case_id"])
        rows.append(case)
    return rows
