# /app/backend/app/jobs/nightly_refresh.py
from app.jobs.refresh_interfaces import refresh as refresh_interfaces
from app.jobs.refresh_vlans import refresh as refresh_vlans

def main():
    refresh_interfaces()
    refresh_vlans()

if __name__ == "__main__":
    main()
