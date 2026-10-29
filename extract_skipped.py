"""Run pytest and capture skipped test names from the log file."""
import subprocess, sys

result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-rs", "-q", "--tb=no", "-x", "--co"],
    capture_output=True, text=True, timeout=120,
    cwd=r"F:\Users\Administrator\PycharmProjects\PythonProject\jarvis-super-intelligence"
)
print("STDOUT:", result.stdout[-2000:])
print("STDERR:", result.stderr[-500:])
