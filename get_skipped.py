"""Find skipped tests in the test suite."""
import subprocess, sys, os

os.chdir(r"F:\Users\Administrator\PycharmProjects\PythonProject\jarvis-super-intelligence")
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "-rs", "-q", "--tb=no", "-x"],
    capture_output=True, text=True, timeout=300
)
output = result.stdout + result.stderr
# Find skip lines
for line in output.splitlines():
    if "SKIP" in line.upper() or "skip" in line:
        print(line)
print("\n=== Summary ===")
for line in output.splitlines():
    if "passed" in line.lower() or "error" in line.lower() or "failed" in line.lower():
        print(line)
