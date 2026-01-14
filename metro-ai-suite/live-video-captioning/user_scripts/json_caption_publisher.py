from gstgva import VideoFrame
import json
import os
import time


class JsonCaptionPublisher:
    def __init__(self, *args, **kwargs):
        # gvapython passes arg=[...] as positional args to __init__(self, *args)
        # so we accept one optional arg for path
        # Normalize to an absolute path
        self.path = os.path.abspath(args[0].strip())

        # Ensure parent directory exists
        parent_dir = os.path.dirname(self.path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        # Touch/create the file now (append mode creates if missing)
        with open(self.path, "a", encoding="utf-8") as _:
            pass

        print(f"Caption JSONL directory ensured: {self.path}")

    def _now_ms(self):
        # Simple timestamp (milliseconds since epoch)
        return int(time.time() * 1000)

    def process(self, frame: VideoFrame):
        """
        Called for each frame. Extract plain-text caption and append
        a JSON line: {"ts": <ms>, "caption": "<text>"}.
        Skips writing if no caption is present.
        """
        # Extract caption text from messages (JSON strings)
        caption_text = None
        metrics = {}
        for msg in frame.messages():
            try:
                if isinstance(msg, str):
                    data = json.loads(msg)

                    # Collect metrics if present
                    if "metrics" in data:
                        metrics.update(data["metrics"])

                    # Common keys: 'caption', 'text', 'result' (or nested under 'message')
                    caption_text = (
                        data.get("caption") or
                        data.get("text") or
                        data.get("result")
                    )
                    if caption_text is None and isinstance(data.get("message"), dict):
                        caption_text = (
                            data["message"].get("caption") or
                            data["message"].get("text") or
                            data["message"].get("result")
                        )
                    if caption_text:
                        break
            except Exception:
                # Ignore malformed JSON or other shapes
                pass


        # If caption present, write JSON line
        if caption_text:
            payload = {
                # "ts": self._now_ms(),
                "metrics": metrics,
                "result": caption_text,
            }
            # Append one line per frame
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        # Return True to keep pipeline flowing
        return True
