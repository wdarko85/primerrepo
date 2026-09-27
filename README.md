# primerrepo

## Proyectos

Los proyectos se incluyen como [git submodules](https://git-scm.com/book/es/v2/Herramientas-de-Git-Subm%C3%B3dulos). El código sigue viviendo en su repositorio original (privado); aquí solo se guarda una referencia.

| Proyecto | Carpeta | Repositorio |
| --- | --- | --- |
| horarios-cine | [`horarios-cine/`](horarios-cine) | [wdarko85/horarios-cine](https://github.com/wdarko85/horarios-cine) |

Para clonar con los proyectos incluidos (requiere acceso a los repos privados):

```sh
git clone --recurse-submodules https://github.com/wdarko85/primerrepo.git
# o, si ya lo tienes clonado:
git submodule update --init
```

Para actualizar un proyecto a su última versión:

```sh
git submodule update --remote horarios-cine
git commit -am "Actualizar horarios-cine"
```
