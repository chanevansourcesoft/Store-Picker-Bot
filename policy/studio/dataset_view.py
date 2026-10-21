"""Episodes of one recorded dataset for the app's review: list with plan info and automatic
warnings, joint data, review decisions, and where each episode sits in the camera videos.

LeRobot (v3 format) keeps all episodes of a camera in a few large .mp4 files; meta/episodes
says which file and which time range belongs to each episode, so the review plays that range
straight from the dataset. Review decisions are stored in meta/episode_log.json (the file
record.py writes): "approved", and the "bad" mark that record.py's key B also sets, plus the
reason. remove_bad_episodes.py removes the bad ones later.
"""

import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd

LOG_FILE = "episode_log.json"
CAMERAS = ["scene_cam", "gripper_cam"]  # order in the review: scene first
# Every episode starts and ends in the rest pose. The usual start (median over the dataset) stands
# for it; an episode whose start or end is further than this on any arm joint gets a warning.
# Units are LeRobot's (-100..100 per arm joint); the gripper is left out.
REST_TOLERANCE = 20.0
MIN_EPISODES_FOR_REST = 5
MIN_SECONDS = 2.0


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def file_stamp(paths):
    """Changes whenever one of the files changes (cache key)."""
    return tuple(sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths))


class DatasetView:
    def __init__(self, root: Path, long_episode_s):
        self.root = root
        self.long_episode_s = long_episode_s
        self.lock = threading.Lock()
        self._episodes = (None, None)  # (stamp, dataframe)
        self._data = (None, None)  # (stamp, {episode_index: (state, action)})

    # --- files -------------------------------------------------------------
    def info(self):
        return json.loads((self.root / "meta" / "info.json").read_text())

    def _episode_table(self):
        files = sorted((self.root / "meta" / "episodes").glob("*/*.parquet"))
        stamp = file_stamp(files)
        if self._episodes[0] != stamp:
            columns = ["episode_index", "length"] + [
                f"videos/observation.images.{cam}/{k}" for cam in CAMERAS
                for k in ("chunk_index", "file_index", "from_timestamp", "to_timestamp")]
            table = pd.concat([pd.read_parquet(f, columns=columns) for f in files]) if files else pd.DataFrame()
            self._episodes = (stamp, table.sort_values("episode_index") if len(table) else table)
        return self._episodes[1]

    def _joint_data(self):
        """{episode_index: (state, action)} as float arrays of shape (frames, 6)."""
        files = sorted((self.root / "data").glob("*/*.parquet"))
        stamp = file_stamp(files)
        if self._data[0] != stamp:
            per_episode = {}
            for f in files:
                df = pd.read_parquet(f, columns=["episode_index", "observation.state", "action"])
                for index, group in df.groupby("episode_index"):
                    per_episode[int(index)] = (np.stack(group["observation.state"].to_numpy()),
                                               np.stack(group["action"].to_numpy()))
            self._data = (stamp, per_episode)
        return self._data[1]

    def log(self):
        path = self.root / "meta" / LOG_FILE
        return json.loads(path.read_text()) if path.exists() else {"sessions": [], "episodes": []}

    def video_path(self, cam, chunk, file):
        if cam not in CAMERAS:
            raise ValueError(f"unknown camera {cam!r}")
        path = self.root / "videos" / f"observation.images.{cam}" / f"chunk-{int(chunk):03d}" / f"file-{int(file):03d}.mp4"
        if not path.is_file():
            raise FileNotFoundError(f"no video {path.name}")
        return path

    # --- episodes ----------------------------------------------------------
    def list(self):
        with self.lock:
            table = self._episode_table()
            data = self._joint_data()
            fps = self.info()["fps"]
            log = {e["episode_index"]: e for e in self.log()["episodes"]}
            starts = [state[0, :5] for state, _ in data.values() if len(state)]
            rest = np.median(starts, axis=0) if len(starts) >= MIN_EPISODES_FOR_REST else None
            rows = []
            for row in table.to_dict("records"):
                index = int(row["episode_index"])
                entry = log.get(index, {})
                frames = int(row["length"])
                videos = {}
                for cam in CAMERAS:
                    prefix = f"videos/observation.images.{cam}/"
                    chunk, file = int(row[prefix + "chunk_index"]), int(row[prefix + "file_index"])
                    try:
                        stamp = self.video_path(cam, chunk, file).stat().st_mtime_ns
                    except FileNotFoundError:
                        continue
                    videos[cam] = {"url": f"/video/{cam}/{chunk}/{file}?v={stamp}",
                                   "from": float(row[prefix + "from_timestamp"]),
                                   "to": float(row[prefix + "to_timestamp"])}
                state = data.get(index, (np.zeros((0, 6)), None))[0]
                rows.append({
                    "index": index, "frames": frames, "fps": fps, "seconds": frames / fps,
                    "item": entry.get("item"), "shelf": entry.get("shelf"), "round": entry.get("round"),
                    "position": entry.get("position"), "approved": bool(entry.get("approved")),
                    "bad": bool(entry.get("bad")), "reason": entry.get("bad_reason", ""),
                    "in_log": bool(entry), "videos": videos,
                    "warnings": self._warnings(frames / fps, state, rest),
                })
            return rows

    def _warnings(self, seconds, state, rest):
        """Automatic checks shown in the review; they never block anything."""
        warnings = []
        if seconds < MIN_SECONDS:
            warnings.append("very short")
        if seconds > self.long_episode_s:
            warnings.append(f"longer than {self.long_episode_s:g} s")
        if rest is not None and len(state):
            if np.abs(state[0, :5] - rest).max() > REST_TOLERANCE:
                warnings.append("does not start in the usual rest pose")
            if np.abs(state[-1, :5] - rest).max() > REST_TOLERANCE:
                warnings.append("does not end in the usual rest pose")
        return warnings

    def joints(self, index):
        with self.lock:
            data = self._joint_data()
        if index not in data:
            raise FileNotFoundError(f"no episode {index}")
        state, action = data[index]
        names = [n.removesuffix(".pos") for n in self.info()["features"]["action"]["names"]]
        return {"names": names,
                "state": np.round(state.T, 2).tolist(), "action": np.round(action.T, 2).tolist()}

    # --- review decisions --------------------------------------------------
    def decide(self, index, decision, reason=""):
        """approve, flag (= mark bad: the next round records it again) or clear."""
        with self.lock:
            path = self.root / "meta" / LOG_FILE
            log = self.log()
            entry = next((e for e in log["episodes"] if e["episode_index"] == index), None)
            if entry is None:
                raise ValueError(f"Episode {index} is not in the episode log, so it cannot be reviewed here.")
            entry["approved"] = decision == "approve"
            entry["bad"] = decision == "flag"
            if decision == "flag":
                entry["bad_reason"] = str(reason)[:200]
            else:
                entry.pop("bad_reason", None)
            write_json(path, log)
