"""One automatic installation review per project identity and turn.

Records live with the target's install evidence, independently of WF_LOCAL_DIR.
An interrupted or failed attempt requires an explicit fresh CLI review/install;
automatic reentry never sends the runner again or guesses whether install ran.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
from pathlib import Path

from . import anomaly, core, ops, release, review
from .core import Instance, WfError


def author_for(inst):
    """Use the registered conversation first; optional explicit host binding."""
    lanes = review._lanes()
    conv = (inst.state.get("conv") or {}).get("conv")
    matches = [name for name, lane in lanes.items()
               if isinstance(name, str) and isinstance(lane, dict)
               and isinstance(lane.get("sessions"), list) and conv
               and conv in lane["sessions"]]
    if len(matches) > 1:
        raise WfError("对话匹配多个作者车道（author_identity_ambiguous），请核对当前 lanes.json")
    cfg = anomaly.transport_cfg().get("driver_review")
    explicit = None
    if cfg is not None:
        if (not isinstance(cfg, dict) or cfg.get("schema") != "driver-review/1"
                or not isinstance(cfg.get("author"), str) or not cfg["author"].strip()):
            raise WfError("driver_review 配置须为 driver-review/1 并声明 author（author_identity_missing）")
        explicit = cfg["author"]
    author = matches[0] if matches else explicit
    if matches and explicit and explicit != author:
        raise WfError("已绑定对话与 driver_review.author 不一致（author_identity_ambiguous）")
    lane = lanes.get(author) if author else None
    sessions = lane.get("sessions") if isinstance(lane, dict) else None
    if (not author or not isinstance(sessions, list) or not sessions
            or any(not isinstance(s, str) or not s.strip() for s in sessions)):
        raise WfError("无法核对作者（author_identity_missing）：用 wf conv bind 绑定 lanes.json 中唯一作者的会话，"
                      "或在宿主 transport.json 配置 driver_review（schema=driver-review/1, author=已登记车道）")
    return author


class Attempt:
    """Target-owned nonblocking lock plus append-only, checked phase records."""

    def __init__(self, inst, package):
        self.inst, self.package = inst, Path(package)
        self.fd = self.lockfd = None
        self.binding = self.prefix = self.request_id = None
        self.created_intent = False
        self.records = {}

    def __enter__(self):
        try:
            self.store = release._install_store(self.inst.root, "project")
            self.path = self.store / "driver-review"
            try:
                self.path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.inode = os.fstat(self.fd)
            self.lockfd = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
            if not stat.S_ISREG(os.fstat(self.lockfd).st_mode):
                raise WfError("驱动器审查锁不是普通文件（driver_review_corrupt）")
            try:
                fcntl.flock(self.lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WfError("同一目标已有驱动器安装审查进行（driver_review_busy）")
            self.verify()
            target = review._target(Instance(self.inst.root), "project")
            if not target["project"]["created"]:
                raise WfError("项目缺少 created 身份，不能自动送审（driver_review_target_unknown）")
            turn = self.inst.state.get("turn")
            if type(turn) is not int or turn < 0:
                raise WfError("安装轮次不合法（driver_review_target_unknown）")
            self.identity = {k: target[k] for k in ("kind", "root", "project", "scope", "tool")}
            self.turn = turn
            self.prefix = release._digest({"target": self.identity, "turn": turn})
            prior = self.read("intent")
            if prior is not None:
                self.binding = prior
                req = self.read("request")
                if req and any(req.get(k) != prior.get(k) for k in (
                        "content_id", "package_sha256", "base_sha256")):
                    raise WfError("驱动器审查请求记录与意图不符（driver_review_corrupt）")
                if req and req.get("intent_sha256") != prior["body_sha256"]:
                    raise WfError("驱动器审查请求未绑定意图（driver_review_corrupt）")
                self.request_id = req.get("request_id") if req else None
                raise WfError(self.recovery("本轮已有安装审查意图，不自动重审或重装" + self._close_orphans()))
            if any(n.startswith(self.prefix + "-") for n in os.listdir(self.fd)):
                raise WfError("驱动器审查阶段记录缺少意图（driver_review_corrupt）")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def verify(self):
        release._install_store(self.inst.root, "project", create=False)
        at = os.lstat(self.path)
        if not stat.S_ISDIR(at.st_mode) or (at.st_dev, at.st_ino) != (self.inode.st_dev, self.inode.st_ino):
            raise WfError("驱动器审查记录目录已改变（driver_review_corrupt）")
        at_lock = os.stat("lock", dir_fd=self.fd, follow_symlinks=False)
        held = os.fstat(self.lockfd)
        if (not stat.S_ISREG(at_lock.st_mode)
                or (at_lock.st_dev, at_lock.st_ino) != (held.st_dev, held.st_ino)):
            raise WfError("驱动器审查锁已改变（driver_review_corrupt）")

    @review._boundary("driver_review_read", code="driver_review_corrupt")
    def read(self, phase):
        self.verify()
        name = f"{self.prefix}-{phase}.json"
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        try:
            at = os.fstat(fd)
            if not stat.S_ISREG(at.st_mode) or at.st_size > 2_000_000:
                raise ValueError("not a regular bounded record")
            with os.fdopen(fd, "rb", closefd=False) as fh:
                doc = json.load(fh)
            body = {k: v for k, v in doc.items() if k != "body_sha256"}
            if (doc.get("body_sha256") != release._digest(body)
                    or doc.get("schema") != "driver-install-review/1"
                    or doc.get("phase") != phase or doc.get("target") != self.identity
                    or doc.get("turn") != self.turn):
                raise ValueError("record binding mismatch")
            current = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                    at.st_dev, at.st_ino, at.st_size, at.st_mtime_ns):
                raise ValueError("record changed while reading")
            self.verify()
            return doc
        except (ValueError, TypeError, AttributeError) as e:
            raise WfError(f"驱动器审查记录损坏（driver_review_corrupt）：{name}：{e}")
        finally:
            os.close(fd)

    @review._boundary("driver_review_write", code="driver_review_corrupt")
    def write(self, phase, **values):
        self.verify()
        for recorded_phase, recorded in self.records.items():
            if self.read(recorded_phase) != recorded:
                raise WfError(f"驱动器审查阶段记录已改变（driver_review_corrupt）：{recorded_phase}")
        body = {"schema": "driver-install-review/1", "phase": phase, "target": self.identity,
                "turn": self.turn, "at": review._iso(review._now()), **values}
        if self.binding is not None:
            body["intent_sha256"] = self.binding["body_sha256"]
        doc = dict(body, body_sha256=release._digest(body))
        name = f"{self.prefix}-{phase}.json"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as fh:
                fh.write(review._canon(doc))
                fh.flush()
                os.fsync(fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)
        self.verify()
        if self.read(phase) != doc:
            raise WfError("驱动器审查记录写后核验不符（driver_review_corrupt）")
        self.records[phase] = doc
        return doc

    def reviewed_request(self, timeout_s=None):
        author = author_for(self.inst)
        target = review._target(Instance(self.inst.root), "project")
        package = review.read_package(self.package, kind="project", protect=target["protect"])
        material, _ = review._material(target, package)
        self.binding = self.write("intent", package_sha256=package["sha256"],
                                  content_id=review._content_id(material),
                                  base_sha256=release._digest(material["base"]), author=author)
        self.created_intent = True
        req = review.prepare(self.inst, self.package, kind="project", author=author)
        self.request_id = req["request_id"]
        if (req["target"] != self.identity or req["content_id"] != self.binding["content_id"]
                or req["package"]["sha256"] != self.binding["package_sha256"]
                or release._digest(req["base"]) != self.binding["base_sha256"]):
            raise WfError("冻结期间目标或版本包改变（driver_review_material_changed）")
        self.write("request", request_id=self.request_id, intent_sha256=self.binding["body_sha256"],
                   content_id=req["content_id"], package_sha256=req["package"]["sha256"],
                   base_sha256=release._digest(req["base"]))
        decision = review.run(self.request_id, timeout_s=timeout_s)
        self.write("decision", request_id=self.request_id, decision=decision)
        current = review.status(self.inst, self.request_id, phase="install")
        if not decision.get("allowed") or not current.get("allowed"):
            model = current.get("provenance", {}).get("model_claimed") or "unknown"
            raise WfError(f"强制安装审查拒绝（{self.request_id}, model={model}）："
                          + "、".join(current.get("reasons", [])))
        if author_for(self.inst) != author:
            raise WfError("审查期间作者绑定改变（author_identity_changed）")
        self.verify()
        self.content_id = req["content_id"]
        return self.request_id

    def installing(self, install_exec):
        """持久调用身份分配之后、交给受保护安装器之前登记；身份写进本次尝试的只增记录。"""
        decision = self.records.get("decision")
        if (self.request_id is None or decision is None or decision.get("request_id") != self.request_id
                or not (decision.get("decision") or {}).get("allowed")):
            raise WfError("尚未取得本次安装审查许可（driver_review_incomplete）")
        self.write("installing", request_id=self.request_id, content_id=self.content_id, install_exec=install_exec)

    def _close_orphans(self):
        """持本目标审查锁时，把被杀的驱动器进程内安装调用补记为中断：只记一次，绝不重跑。"""
        try:
            closed = ops.close_orphan_installs(self.inst)
        except Exception as e:
            return f"；遗留安装调用补记失败（install_exec_unrecorded）：{type(e).__name__}: {e}"
        return ("；安装调用 " + "、".join(closed) + " 已补记为中断（install_exec_interrupted）") if closed else ""

    def recovery(self, text):
        return (f"{text}（driver_review_incomplete，request_id={self.request_id or '尚未登记'}）。"
                "核对目标安装记录并修复配置后，先显式执行 wf force APPLY --reason \"已核对安装记录，重新送审\"，"
                "再 wf review prepare <zip> --kind project --author <lane> --json，"
                "再 wf review run <新ID>，最后 wf apply <zip> --review-id <新ID>；或让作者重新交付到新轮次。")

    def complete(self, result):
        installing = self.records.get("installing") or {}
        self.write("closed", request_id=self.request_id, install_tx=result.get("install_tx"),
                   version=result.get("version"), install_ref=result.get("install_ref"),
                   install_exec=installing.get("install_exec"))

    def __exit__(self, typ, error, tb):
        try:
            if error is not None and self.created_intent and self.prefix and self.fd is not None:
                # A record failure must stay a failure; never hide the original.
                try:
                    if self.read("failed") is None:
                        self.write("failed", request_id=self.request_id, error=str(error)[:2000])
                except Exception:
                    pass
        finally:
            if self.lockfd is not None:
                os.close(self.lockfd)
                self.lockfd = None
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
