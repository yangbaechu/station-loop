from __future__ import annotations

import argparse
import os
from pathlib import Path

from .agents import CooperativeAgents, OpenAIAgents, ScriptedAgents
from .replay import replay_run
from .rules import EngineError, make_judge
from .service import Game


def main():
    parser = argparse.ArgumentParser(description="Station Loop — 우주정거장 에이전트 실험실")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "serve", "replay"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--rules", choices=("wirelog", "reference"), default="wirelog")
        if name == "replay":
            cmd.add_argument("path", type=Path)
        else:
            cmd.add_argument("--agents", choices=("cooperative", "scripted", "openai", "chatgpt"),
                             default="scripted" if name == "demo" else "cooperative")
            cmd.add_argument("--model", default=os.environ.get("STATION_LOOP_MODEL"))
            cmd.add_argument("--runs-dir", type=Path)
        if name == "serve":
            cmd.add_argument("--port", type=int, default=8765)
            cmd.add_argument("--auth-dir", type=Path, help="ChatGPT 인증정보 전용 로컬 폴더")
    args = parser.parse_args()
    auth = None
    try:
        judge = make_judge(args.rules)
        if args.command == "replay":
            sim = replay_run(args.path, judge)
            print(f"리플레이 검증 완료: {sim.world['tick']}턴 / {sim.world['status']}")
            return
        if args.command == "serve" or args.agents == "chatgpt":
            from .chatgpt_auth import ChatGPTAuth
            auth = ChatGPTAuth(getattr(args, "auth_dir", None))
        if args.agents == "chatgpt":
            from .chatgpt_agents import ChatGPTAgents
            if args.command == "demo" and not args.model:
                raise ValueError("ChatGPT 터미널 데모에는 --model이 필요합니다. 먼저 브라우저에서 연결해 주세요.")
            provider = ChatGPTAgents(auth, args.model or "")
        elif args.agents == "openai":
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY 환경변수가 필요합니다.")
            provider = OpenAIAgents(args.model)
        else:
            provider = ScriptedAgents() if args.agents == "scripted" else CooperativeAgents()
        game = Game(judge, provider, runs_root=args.runs_dir)
        if args.command == "demo":
            while game.sim.world["status"] == "running":
                tick = game.sim.world["tick"]
                snapshot = game.advance(f"demo-{tick}", tick, game.sim.store.path.name)
                w = snapshot["world"]
                rejected = sum(not e["allowed"] for e in snapshot["records"][-1]["events"])
                print(f"턴 {w['tick']:2} | 산소 {w['resources']['oxygen']:2} | "
                      f"전력 {w['resources']['power']:2} | 연속 생산 {w['production_streak']} | 거절 {rejected}")
            print(f"결과: {w['status']} / {w['termination']}")
            print(f"판정: {judge.identity()}")
            print(f"실행 기록: {game.sim.store.path}")
        else:
            from .web import server_for
            server = server_for(game, args.port, auth)
            print(f"Station Loop: http://127.0.0.1:{server.server_port}", flush=True)
            print(f"판정: {judge.name} / 승무원: {provider.name}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    except (EngineError, ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    except RuntimeError as exc:
        from .chatgpt_auth import AuthError
        if not isinstance(exc, AuthError):
            raise
        parser.exit(1, str(exc) + "\n")
    finally:
        if auth:
            auth.close()


if __name__ == "__main__":
    main()
