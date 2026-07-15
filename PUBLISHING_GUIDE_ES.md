# Guía para publicar Rclone Nexus correctamente en GitHub

Esta guía deja el proyecto listo para alojarlo como código fuente en GitHub y publicar un ZIP instalable para Kodi mediante **GitHub Releases**.

> Importante: publicar en GitHub no equivale a entrar en el repositorio oficial de Kodi. El ZIP de GitHub se instala manualmente y no recibe actualizaciones automáticas, salvo que más adelante crees un repositorio de add-ons o envíes el proyecto al repositorio oficial.

## 1. Crear el repositorio

1. En GitHub, crea un repositorio llamado, por ejemplo, `rclone-nexus-kodi`.
2. No marques las opciones para crear README, licencia o `.gitignore`, porque ya están incluidos.
3. Descomprime el paquete de código fuente y abre una terminal dentro de la carpeta.

```bash
git init
git add .
git commit -m "Initial English US release"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/rclone-nexus-kodi.git
git push -u origin main
```

Cambia `TU_USUARIO` por tu usuario real de GitHub.

## 2. Revisiones antes de publicar

Ejecuta la validación local:

```bash
python scripts/validate.py
```

Crea el ZIP instalable:

```bash
python scripts/build_release.py
```

El archivo resultante aparecerá en:

```text
dist/plugin.ariostv-1.3.1.zip
```

El ZIP instalable debe contener una única carpeta superior llamada `plugin.ariostv`, y dentro de ella debe estar `addon.xml`. No subas a Releases el ZIP de código fuente que GitHub genera automáticamente; adjunta el ZIP creado por el script.

## 3. Publicar la primera versión

El proyecto incluye un flujo de GitHub Actions que crea la Release automáticamente al enviar una etiqueta que empiece por `v`.

```bash
git tag v1.3.1
git push origin v1.3.1
```

Después:

1. Abre la pestaña **Actions** y verifica que el flujo **Release** termine correctamente.
2. Abre **Releases**.
3. Confirma que esté adjunto `plugin.ariostv-1.3.1.zip`.
4. Añade una descripción breve basada en `CHANGELOG.md`.

## 4. Configurar bien la página del repositorio

En **About**, usa algo similar a:

```text
Kodi Android add-on for browsing rclone remotes, streaming media, and building incremental STRM libraries.
```

Temas recomendados:

```text
kodi, kodi-addon, rclone, strm, android, fire-tv, cloud-storage, python
```

Activa **Issues** para recibir reportes. En cada reporte pide como mínimo:

- Versión de Kodi.
- Dispositivo y versión de Android o Fire OS.
- Arquitectura de Kodi: ARM32, ARM64, x86 o x86_64.
- Versión de rclone.
- Tipo de remote.
- Registro de Kodi con datos sensibles eliminados.

## 5. Probar el ZIP antes de anunciarlo

Prueba la Release en una instalación limpia de Kodi:

1. Habilita **Settings > System > Add-ons > Unknown sources**.
2. Abre **Add-ons > Install from ZIP file**.
3. Instala `plugin.ariostv-1.3.1.zip`.
4. Comprueba todas las pantallas y opciones en inglés.
5. Configura `rclone.conf` y prueba listado, reproducción, búsqueda, favoritos, exportación STRM y sincronización.
6. Reinicia Kodi y verifica el servicio automático con la opción desactivada y activada.
7. Comprueba que el almacenamiento temporal no crezca sin límite.

Kodi documenta que el manifiesto `addon.xml` debe estar en la raíz de la carpeta del add-on y que la instalación manual se realiza desde un ZIP. Referencias oficiales:

- https://kodi.wiki/view/Addon.xml
- https://kodi.wiki/view/Add-on_structure
- https://kodi.wiki/view/Add-on_manager

## 6. Publicar actualizaciones

Nunca sustituyas un ZIP manteniendo el mismo número de versión. Para cada cambio:

1. Incrementa `version` en `addon.xml`.
2. Actualiza el encabezado de versión en `addon.py`.
3. Añade la nueva sección en `CHANGELOG.md`.
4. Ejecuta validación y construcción.
5. Crea y envía una etiqueta que coincida con la versión.

Ejemplo para `1.3.2`:

```bash
git add .
git commit -m "Release 1.3.2"
git push
git tag v1.3.2
git push origin v1.3.2
```

## 7. Sobre el binario rclone

El código fuente entregado no incluye el ejecutable de rclone. Esto evita publicar accidentalmente una arquitectura incorrecta y mantiene pequeño el repositorio.

Antes de crear una Release con binario integrado, coloca únicamente el ejecutable necesario en una de estas rutas:

```text
resources/bin/android/armeabi-v7a/rclone
resources/bin/android/arm64-v8a/rclone
resources/bin/android/x86/rclone
resources/bin/android/x86_64/rclone
```

También se acepta `rclone.gz`. Verifica la licencia y los avisos de distribución aplicables a la versión de rclone que incluyas.

## 8. Repositorio oficial de Kodi: trabajo adicional

La versión preparada aquí es adecuada para GitHub Releases y para instalación manual. Para solicitar inclusión en el repositorio oficial de Kodi debes revisar las reglas vigentes, ejecutar Kodi Addon-Checker y adaptar todos los textos visibles al sistema de localización de Kodi mediante `strings.po`; las reglas oficiales indican que no deben existir cadenas visibles codificadas directamente.

Referencias oficiales:

- https://kodi.wiki/view/Add-on_rules
- https://github.com/xbmc/repo-plugins/blob/master/CONTRIBUTING.md
- https://github.com/xbmc/repo-plugins

También debes revisar si se permiten binarios integrados en el destino de publicación. Las reglas oficiales de Kodi normalmente rechazan binarios dentro de los add-ons del repositorio oficial, por lo que ese punto requiere una estrategia distinta.

## 9. Lista final de comprobación

- [ ] `python scripts/validate.py` finaliza correctamente.
- [ ] El ZIP se construye con `python scripts/build_release.py`.
- [ ] La versión de `addon.xml` coincide con la etiqueta de Git.
- [ ] El ZIP abre con la carpeta superior `plugin.ariostv`.
- [ ] No hay contraseñas, tokens, remotes privados ni `rclone.conf` en el repositorio.
- [ ] El ZIP se instala y funciona en Kodi.
- [ ] La Release contiene el ZIP instalable, no solo el código fuente automático de GitHub.
- [ ] `README.md`, `CHANGELOG.md`, `LICENSE.txt` y `NOTICE.md` están visibles.
