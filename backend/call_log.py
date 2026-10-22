import csv
import os
from datetime import datetime

import settings

FIELDS = ["time", "caller", "called", "texted"]


def log_call(caller, called, texted):
    """Add one missed call to the CSV log, creating the file if needed."""
    new_file = not os.path.exists(settings.CALL_LOG_FILE)

    with open(settings.CALL_LOG_FILE, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "caller": caller,
                "called": called,
                "texted": texted,
            }
        )
