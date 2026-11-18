# HTTP service evaluation

Read the service's startup and routing code. Start it inside the sandbox, redirecting background stdout and stderr to files. Probe readiness over localhost before testing routes. Python's `urllib.request` and Node's `fetch` are available without additional dependencies. Exercise request/response semantics and verify mutations with subsequent reads. Inspect service logs when requests fail and distinguish startup or harness mistakes from implementation defects. Keep all clients and servers inside the sandbox; external network access is disabled.
