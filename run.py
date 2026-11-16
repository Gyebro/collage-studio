"""Launch the collage editor and open it in your default browser."""
import os
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.getenv("COLLAGE_PORT", "8000"))
    url = f"http://{host}:{port}"
    print(f"\nCollage Studio is running at {url}\nPress Ctrl+C to stop.\n")
    if os.environ.get("COLLAGE_NO_BROWSER", "0") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("main:app", host=host, port=port)
