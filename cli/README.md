# frostbyte-cli

CLI para que agentes y personas lean y editen Frostbyte por su API REST: carta e imágenes, productos y variantes, ventas, finanzas, pedidos y estado del local. Un solo archivo, Node 20+, sin dependencias.

```bash
ln -sf "$PWD/cli/frostbyte.mjs" ~/.local/bin/frostbyte   # desde la raíz del repo
frostbyte login --username <admin>
frostbyte --help
frostbyte schema        # contrato para agentes
```

- Por defecto apunta a producción. `frostbyte config --url local` (o `--url local` por comando) usa el backend local en `:18000`.
- La sesión (refresh token rotado, nunca la contraseña) vive en `~/.config/frostbyte/sessions/` (permisos 0600). Varios procesos pueden renovarla a la vez sin perderla, y una renovación que llega tarde no revive una sesión cerrada.
- Las escrituras aceptan `--dry-run` y quedan en `~/.local/state/frostbyte/audit/AAAA-MM-DD.jsonl` (pendiente antes de la llamada, resultado después).
- El manual para agentes está en `skills/frostbyte/SKILL.md`.
