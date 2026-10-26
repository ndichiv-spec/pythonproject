import sys
sys.stdout.reconfigure(encoding='utf-8')
with open('app/ui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _theme() calls in chat_page
for i, line in enumerate(lines, 1):
    if '_theme()' in line or 'def chat_page' in line or 'def _gate' in line or 'def _theme' in line:
        print(f"L{i}: {line.rstrip()[:180]}")

# Show the _theme function
print("\n=== _theme function ===")
for i, line in enumerate(lines, 1):
    if 'def _theme' in line:
        end = min(i + 20, len(lines))
        for j in range(i-1, end):
            print(f"L{j+1}: {lines[j].rstrip()[:180]}")
        break

# Show chat_page start (after _gate)
print("\n=== chat_page after _gate ===")
for i, line in enumerate(lines, 1):
    if 'def chat_page' in line:
        end = min(i + 15, len(lines))
        for j in range(i-1, end):
            print(f"L{j+1}: {lines[j].rstrip()[:180]}")
        break
