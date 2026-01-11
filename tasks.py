from invoke import task
from datetime import datetime, timedelta
import pytz


@task
def countdown(c):
    """
    Shows time remaining until the next market open.
    """
    market_configs = {
        "nikkei": {"hour": 8, "minute": 55, "timezone": "Asia/Tokyo"},
        "australia": {"hour": 9, "minute": 55, "timezone": "Australia/Sydney"},
        "london": {"hour": 7, "minute": 55, "timezone": "Europe/London"},
        "germany": {"hour": 7, "minute": 55, "timezone": "Europe/London"},
        "ny": {"hour": 9, "minute": 25, "timezone": "America/New_York"},
        "us_tech": {"hour": 9, "minute": 25, "timezone": "America/New_York"},
    }

    now_utc = datetime.now(pytz.utc)
    next_opens = []

    for name, cfg in market_configs.items():
        tz = pytz.timezone(cfg["timezone"])
        now_tz = now_utc.astimezone(tz)

        # Calculate next occurrence of this time
        target = now_tz.replace(
            hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0
        )

        # If it's already passed today, or it's a weekend, find the next Mon-Fri open
        if now_tz >= target or now_tz.weekday() >= 5:
            # Simple logic: keep adding days until we find a weekday open that is in the future
            days_to_add = 1
            while True:
                candidate = target + timedelta(days=days_to_add)
                if candidate.weekday() < 5 and candidate > now_tz:
                    target = candidate
                    break
                days_to_add += 1

        next_opens.append((name, target.astimezone(pytz.utc)))

    next_opens.sort(key=lambda x: x[1])
    next_market, next_time = next_opens[0]
    remaining = next_time - now_utc

    # Clean up the string representation
    rem_str = str(remaining).split(".")[0]

    print(f"\nNext Open: {next_market.upper()}")
    print(f"Time:      {next_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Countdown: T-{rem_str}\n")


@task
def deploy(c):
    """
    Build the Docker image and push it to the registry.
    """
    registry = "192.168.0.191:5000"
    image_name = "trader"
    tag = "latest"
    full_image_name = f"{registry}/{image_name}:{tag}"

    # Get current git commit hash
    git_sha = c.run("git rev-parse HEAD", hide=True).stdout.strip()

    print(f"Building {full_image_name} (SHA: {git_sha[:7]})...")
    c.run(f"docker build --build-arg GIT_COMMIT_SHA={git_sha} -t {full_image_name} .")

    print(f"Pushing {full_image_name}...")
    c.run(f"docker push {full_image_name}")
