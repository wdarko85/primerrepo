# primerrepo
## Registro automático de sesiones de Claude Code en Obsidian

`.claude/settings.json` registra un hook (`Stop` y `SessionEnd`) que ejecuta
`.claude/hooks/obsidian_session_log.py`. Tras cada respuesta de Claude crea o
actualiza una nota por sesión en tu vault con:

- título de la sesión, proyecto, rama y `session_id` (en el frontmatter, útil para Dataview)
- las peticiones que hiciste
- los archivos modificados
- los commits hechos durante la sesión
- la última respuesta de Claude

### Configuración

| Variable | Obligatoria | Descripción |
|---|---|---|
| `OBSIDIAN_VAULT` | sí | Ruta al vault. Si no está definida el hook no hace nada. |
| `OBSIDIAN_FOLDER` | no | Carpeta de las notas (por defecto `Claude Sessions`). |
| `OBSIDIAN_DAILY_FOLDER` | no | Si se define (p. ej. `Daily`), añade un enlace a la nota en la daily note del día. |
| `OBSIDIAN_GIT_PUSH` | no | `1` para hacer `commit` + `push` si el vault es un repo git (plugin Obsidian Git). |

**En tu ordenador:** añade las variables a `~/.claude/settings.json`:

```json
{ "env": { "OBSIDIAN_VAULT": "/Users/tu-usuario/Obsidian/MiVault", "OBSIDIAN_DAILY_FOLDER": "Daily" } }
```

Para usarlo en **todos** tus proyectos, copia el script a `~/.claude/hooks/` y
pon el bloque `hooks` en `~/.claude/settings.json` apuntando a esa ruta.

Las notas se guardan agrupadas por proyecto:
`Claude Sessions/<proyecto>/<fecha> <título de la sesión>.md`. El proyecto sale del
repo git; si abriste Claude en una carpeta genérica (Documents, tu carpeta de
usuario...) se deduce de los archivos que se tocaron, o va a `General`.
Al volver a ejecutar `--backfill`, las notas con el formato antiguo se renombran.

### Importar sesiones anteriores

Para volcar al vault todas las sesiones que ya tienes en `~/.claude/projects`
(de todos los proyectos, con su fecha real):

```bash
OBSIDIAN_VAULT=/ruta/a/tu/vault OBSIDIAN_DAILY_FOLDER=Daily \
  python3 ~/.claude/hooks/obsidian_session_log.py --backfill
```

Se puede repetir sin miedo: las sesiones ya importadas se actualizan, no se duplican.

**En Claude Code en la web (sesiones en la nube):** el contenedor no ve tu disco,
así que el vault tiene que ser un repo de GitHub sincronizado con el plugin
Obsidian Git. En el script de setup del entorno clona el vault y define
`OBSIDIAN_VAULT` apuntando al clon y `OBSIDIAN_GIT_PUSH=1`.
