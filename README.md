correr db

```bash
docker compose -f docker-compose.dev.yml up -d db
```

generar requirements.txt

```bash
source .venv/bin/activate && rm requirements.txt && uv pip compile pyproject.toml -o requirements.txt
```
