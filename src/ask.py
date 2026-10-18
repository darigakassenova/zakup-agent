import argparse
import json

from agent.agent import answer
from agent.db import connect


def show(reply, as_json):
    if as_json and reply["result"] is not None:
        print(json.dumps(reply["result"], ensure_ascii=False, indent=1, default=str))
    else:
        print(reply["text"])


def interactive(conn, use_llm, as_json):
    while True:
        try:
            question = input("\nВопрос (пустая строка — выход): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        try:
            show(answer(question, conn, use_llm=use_llm), as_json)
        except Exception as error:
            print(f"Ошибка при обработке вопроса: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--no-llm", action="store_true", help="разбирать вопрос правилами, без языковой модели")
    parser.add_argument("--json", action="store_true", help="вывести результат инструмента в JSON")
    args = parser.parse_args()

    with connect() as conn:
        if args.question:
            show(answer(" ".join(args.question), conn, use_llm=not args.no_llm), args.json)
        else:
            interactive(conn, use_llm=not args.no_llm, as_json=args.json)


if __name__ == "__main__":
    main()