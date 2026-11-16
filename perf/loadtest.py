"""Load test: many virtual users hitting a running server at once.

Each scenario runs for DURATION seconds with CONCURRENCY users and reports
throughput and latency percentiles. Run it against the baseline and the tuned
server on identical copies of the same database.

Usage: python perf/loadtest.py <base_url> <db_file> <out.json> [concurrency] [duration]
"""
import asyncio
import json
import random
import sqlite3
import statistics
import sys
import time
import uuid

import httpx

PASSWORD = "Demo@1234"


def pick_targets(db_file, n_users):
    c = sqlite3.connect(db_file)
    hot = c.execute("SELECT a.customer_id, a.id, COUNT(*) n FROM transactions t "
                    "JOIN accounts a ON a.id = t.account_id GROUP BY a.id "
                    "ORDER BY n DESC LIMIT ?", (n_users,)).fetchall()
    normal = c.execute("SELECT customer_id, id FROM accounts WHERE id IN (SELECT MIN(id) "
                       "FROM accounts GROUP BY customer_id) ORDER BY customer_id LIMIT ?",
                       (n_users * 4,)).fetchall()
    payees = [r[0] for r in c.execute("SELECT number FROM accounts LIMIT 200")]
    return hot, normal, payees


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


async def run_scenario(base, name, make_request, sessions, concurrency, duration):
    lat, errors = [], 0
    stop = time.perf_counter() + duration

    async def user(i):
        nonlocal errors
        client, ctx = sessions[i % len(sessions)]
        while time.perf_counter() < stop:
            method, url, body = make_request(ctx)
            t0 = time.perf_counter()
            r = await client.request(method, url, json=body)
            lat.append((time.perf_counter() - t0) * 1000)
            if r.status_code >= 500 or (r.status_code >= 400 and name != "transfer"):
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(user(i) for i in range(concurrency)))
    wall = time.perf_counter() - t0
    return {"scenario": name, "requests": len(lat), "rps": round(len(lat) / wall, 1),
            "p50_ms": round(pct(lat, 50), 1), "p95_ms": round(pct(lat, 95), 1),
            "p99_ms": round(pct(lat, 99), 1), "mean_ms": round(statistics.mean(lat), 1),
            "errors": errors}


async def main(base, db_file, out, concurrency=20, duration=20):
    hot, normal, payees = pick_targets(db_file, concurrency)
    limits = httpx.Limits(max_connections=concurrency * 2)

    async def session(cid, aid):
        c = httpx.AsyncClient(base_url=base, timeout=60, limits=limits)
        r = await c.post("/api/login", json={"customer_id": cid, "password": PASSWORD})
        r.raise_for_status()
        c.headers["Authorization"] = "Bearer " + r.json()["token"]
        first = (await c.get(f"/api/accounts/{aid}/statement?limit=25")).json()
        return c, {"aid": aid, "cursor": first["next_cursor"]}

    hot_s = [await session(cid, aid) for cid, aid, _ in hot]
    norm_s = [await session(cid, aid) for cid, aid in normal]

    def first_page(ctx):
        return "GET", f"/api/accounts/{ctx['aid']}/statement?limit=25", None

    def deep_page(ctx):          # page ~200 of a busy account: the classic OFFSET trap
        if ctx.get("deep") is None:
            return first_page(ctx)
        return "GET", f"/api/accounts/{ctx['aid']}/statement?limit=25&cursor={ctx['deep']}", None

    def search(ctx):
        return "GET", f"/api/accounts/{ctx['aid']}/statement?limit=25&q=salary", None

    def transfer(ctx):
        return "POST", "/api/transfers", {
            "from_account_id": ctx["aid"], "to_account_number": random.choice(payees),
            "amount": f"{random.randint(1, 500)}.00", "description": "load test",
            "idempotency_key": uuid.uuid4().hex}

    # cursor for page 201 (rows 5,001+), computed from the database so no timed run pays for it
    c = sqlite3.connect(db_file)
    for _, ctx in hot_s:
        if ctx["cursor"] and "|" in ctx["cursor"]:          # tuned server: keyset cursor
            ts, tid = c.execute("SELECT ts, id FROM transactions WHERE account_id = ? "
                                "ORDER BY ts DESC, id DESC LIMIT 1 OFFSET 4999",
                                (ctx["aid"],)).fetchone()
            ctx["deep"] = f"{ts}|{tid}"
        else:                                                # baseline server: offset cursor
            ctx["deep"] = "5000"

    results = []
    for name, req, sess in (("statement_first_page", first_page, hot_s),
                            ("statement_page_200", deep_page, hot_s),
                            ("statement_search", search, hot_s),
                            ("statement_normal_account", first_page, norm_s),
                            ("transfer", transfer, norm_s)):
        res = await run_scenario(base, name, req, sess, concurrency, duration)
        results.append(res)
        print(json.dumps(res), flush=True)
    for c, _ in hot_s + norm_s:
        await c.aclose()
    json.dump({"concurrency": concurrency, "duration_s": duration, "results": results},
              open(out, "w"), indent=1)


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1], a[2], *(int(x) for x in a[3:5])))
