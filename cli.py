import os
from colorama import init, Fore, Style

init(autoreset=True)

EXIT_WORDS = {"q", "quit", "exit", "back"}

class BackToMenu(Exception):
    pass

def clear():
    os.system("cls" if os.name == "nt" else "clear")

def pause():
    input("\nPress ENTER to return to menu...")

def line():
    print("=" * 56)

def section(title):
    print()
    line()
    print(Style.BRIGHT + title)
    line()

def ok(msg):
    print(f"{Fore.GREEN}[OK] {msg}")

def warn(msg):
    print(f"{Fore.YELLOW}[WARN] {msg}")

def fail(msg):
    print(f"{Fore.RED}[FAIL] {msg}")

def red(text):
    return f"{Fore.RED}{text}{Style.RESET_ALL}"

def yellow(text):
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"

def green(text):
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}"

def ask(prompt, allow_empty=False):
    value = input(prompt).strip()
    if value.lower() in EXIT_WORDS:
        raise BackToMenu()
    if not value and not allow_empty:
        print("Value required. Type 'back' to return to menu.")
        return ask(prompt, allow_empty=allow_empty)
    return value

def yes_no(prompt):
    while True:
        value = ask(prompt).lower()
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("Enter y or n. Type 'back' to return to menu.")

def print_table(header, rows, col_widths):
    sep = "  "
    fmt = sep.join(f"{{{i}:{w}s}}" for i, w in enumerate(col_widths))
    print(fmt.format(*header))
    print(sep.join("-" * w for w in col_widths))
    for row in rows:
        print(fmt.format(*row))

def print_summary(success, warnings, failed, total):
    section("SUMMARY")
    print(f"Successful:              {success}")
    print(f"Completed with warnings: {warnings}")
    print(f"Failed:                  {failed}")
    print(f"Total processed:         {total}")

def explain_create_error(status, text):
    lower = (text or "").lower()
    if status == 401:
        return "Authentication failed. Check the PAT."
    if status == 403:
        if "seat" in lower or "limit" in lower or "billing" in lower:
            return "No seats available or billing limit reached."
        return "Permission denied."
    if status == 409 or status == 422:
        if "exists" in lower:
            return "User already exists."
        return "Siperb rejected the request."
    return f"API failed. Status={status}."
