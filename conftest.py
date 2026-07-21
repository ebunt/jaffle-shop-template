def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 5:  # no tests collected — treat as pass until a test suite exists
        session.exitstatus = 0
