# Optimizador CDI

Aplicacion de escritorio para organizar archivos `.len` de separaciones RIP en planchas rectangulares. La interfaz compara una placa unica con una placa principal y un retazo de dimensiones independientes, unidos por el lado vertical y alineados por la base.

## Instalacion

Requiere Python 3.10 o posterior.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

## Ejecutable de Windows

En Windows, instala las dependencias de empaquetado y genera un ejecutable independiente:

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --windowed --onefile --name OptimizadorCDI-final-v41 --icon app.ico --add-data "logo.png;." --add-data "app.ico;." main.py
```

El archivo se genera en `dist/OptimizadorCDI-final-v41.exe`. El ejecutable se puede copiar a otra computadora Windows sin instalar Python; la carpeta RIP debe configurarse en Preferencias en cada equipo.

### Tipos de fotopolímero

Los archivos LEN pueden usar la nomenclatura `papeleta-nombre_FP45_tinta.len`, `papeleta-nombre_FP67_tinta.len` o `papeleta-nombre_FP112_tinta.len`. El árbol RIP los separa visualmente por tipo y conserva el nombre del trabajo sin ese sufijo. El programa no calcula acomodos ni sugerencias que mezclen tipos de fotopolímero. Para los retazos se compara el código numérico delimitado, por lo que `FP67` es compatible con nombres como `67 HUAHUANG` o `HUAHUANG 67`; el resto del nombre puede identificar la marca. Las búsquedas de retazos siempre consideran todos los LEN disponibles, sin depender de las casillas marcadas. Los archivos antiguos que no incluyan el sufijo siguen funcionando, pero su material debe confirmarse antes del corte.

La carpeta de salida del RIP, las dimensiones maximas del tambor, la tolerancia de corte y el ratio maximo alto/ancho se configuran en **Preferencias**. El ratio maximo inicia en 3:1 y se puede ajustar entre 1:1 y 10:1. **Calcular acomodo** genera hasta cuatro opciones geometricas con miniaturas pequeñas en el panel de controles; al elegir una, se ve ampliada en el lienzo central. Los cuatro perfiles iniciales se evalúan en paralelo para lotes de 16 o mas grabados, con un maximo de cuatro procesos; los lotes menores y las variantes adicionales conservan el procesamiento secuencial. La barra de progreso muestra etapa y porcentaje centrados. **Buscar mas variantes** prueba hasta cinco perfiles adicionales, de uno en uno, e incorpora como maximo dos opciones nuevas por intento, con un limite de diez opciones visibles. **Sugerir combinaciones** reutiliza resultados mientras los archivos y parametros no cambien; la caché conserva hasta cuatro conjuntos recientes durante la sesión. En Preferencias se puede activar el precálculo de sugerencias y configurar la inactividad entre 1 y 120 minutos (5 por defecto); corre con prioridad baja, se cancela ante actividad y se promueve al primer plano si se pulsa **Sugerir combinaciones**. Solo se guarda el resultado completo: una tarea cancelada se recalcula desde cero cuando vuelva a quedar inactiva. La agrupación experimental de tamaños cercanos está desactivada por defecto y acepta tolerancia máxima de 1, 2 o 3 mm por dimensión; las piezas conservan sus medidas reales al empaquetarse y el detalle de la propuesta separa coincidencias exactas de piezas admitidas por tolerancia. **Ver resumen** consolida los trabajos que comparten número de papeleta, muestra sus nombres y suma las tintas seleccionadas respecto del total disponible. **Ctrl+Z** y **Ctrl+Y** deshacen y rehacen cambios de selección en el árbol; limpiar o seleccionar visibles añade un estado al historial y no lo borra. La profundidad se configura en Preferencias (25 cambios por defecto); el historial se reinicia al refrescar la carpeta RIP. Las sugerencias mantienen el ranking geométrico por eficiencia, agrupación dimensional y tamaño. Los errores tecnicos se registran en `%LOCALAPPDATA%/MTRemozar/OptimizadorCDI/logs/optimizador.log`, con rotacion y tres copias de respaldo. La carpeta RIP se vigila para retirar automaticamente los `.len` borrados y limpiar su seleccion guardada. Los calculos se pueden cancelar; al cerrar durante un calculo, la aplicacion solicita su cancelacion y libera el observador de carpetas.

El inventario de retazos se administra desde el menú **Retazos** y se guarda en `%LOCALAPPDATA%/MTRemozar/OptimizadorCDI/retazos.sqlite`, separado de los cálculos normales. Cada registro contiene ancho y alto físicos del retazo y un material elegido del catálogo; puedes gestionar materiales desde **Materiales…**, y los nombres existentes se sugieren al escribir. La actualización crea el catálogo y conserva automáticamente los materiales de inventarios anteriores. Añadir, modificar e importar no registra consumo. **Buscar acomodo en retazos** busca si todas las tintas seleccionadas caben en cada retazo por separado. Aplica los márgenes, pinza, tolerancia y separación configurados, permite girar el retazo y deja la pinza en el lado corto. **Sugerir combinaciones en retazos** genera subconjuntos con el recomendador normal y evalúa cada candidato contra los retazos seleccionados; si no hay tintas marcadas, usa todos los archivos disponibles. La miniatura muestra el retazo físico completo con los grabados compactados al inferior izquierdo; la tabla muestra aparte las dimensiones del corte propuesto. El único porcentaje es el área total de los grabados dividida entre toda el área útil del retazo después de márgenes, pinza y tolerancia; el espacio de separación cuenta como área no aprovechada. La ventana flotante muestra el contador de propuestas y el botón **Detener búsqueda**; el avance detallado permanece en la barra principal. Detener conserva las propuestas encontradas. Los resultados completos se guardan en caché durante la sesión si no cambian archivos, parámetros, retazos ni presupuesto; resultados detenidos no se guardan. El límite es dos veces la última duración medida de **Sugerir combinaciones**, hasta un máximo de 10 minutos (120 segundos iniciales si aún no se midió); la búsqueda corre solo bajo demanda y no compite con el precálculo normal. La heurística no garantiza el óptimo global. Los LEN no identifican el sustrato, así que confirma el material antes del corte. **Marcar retazo usado** elimina el registro sin historial.
El inventario de retazos se administra desde el menú **Retazos** y se guarda en `%LOCALAPPDATA%/MTRemozar/OptimizadorCDI/retazos.sqlite`, separado de los cálculos normales. Cada registro contiene ancho y alto físicos del retazo y un material elegido del catálogo; puedes gestionar materiales desde **Materiales…**, y los nombres existentes se sugieren al escribir. La actualización crea el catálogo y conserva automáticamente los materiales de inventarios anteriores. Añadir, modificar e importar no registra consumo. **Buscar acomodo en retazos** busca si todas las tintas seleccionadas caben en cada retazo por separado. Aplica los márgenes, pinza, tolerancia y separación configurados, permite girar el retazo y deja la pinza en el lado corto. **Sugerir combinaciones en retazos** genera subconjuntos con el recomendador normal y evalúa cada candidato contra los retazos seleccionados. La miniatura muestra el retazo físico completo con los grabados compactados al inferior izquierdo; la tabla muestra aparte las dimensiones del corte propuesto. El único porcentaje es el área total de los grabados dividida entre toda el área útil del retazo después de márgenes, pinza y tolerancia; el espacio de separación cuenta como área no aprovechada. La ventana muestra el avance y cuántas propuestas hay; cancelar conserva las ya encontradas. Los resultados completos se guardan en caché durante la sesión si no cambian archivos, parámetros, retazos ni presupuesto; resultados cancelados o detenidos por tiempo no se guardan. El límite es dos veces la última duración medida de **Sugerir combinaciones**, hasta un máximo de 10 minutos (120 segundos iniciales si aún no se midió); la búsqueda corre solo bajo demanda y no compite con el precálculo normal. La heurística no garantiza el óptimo global. Los LEN no identifican el sustrato, así que confirma el material antes del corte. **Marcar retazo usado** elimina el registro sin historial.

Al elegir **Mostrar acomodo** desde los resultados en retazos, el panel central entra en modo de visualización y conserva el acomodo normal anterior para restaurarlo al salir. **Marcar usado…** permite elegir entre eliminar el retazo completo o actualizar solo las dimensiones del sobrante; **Actualizar sobrante…** también abre directamente esos campos. Al hacer clic en un grabado del lienzo, se resalta su tinta correspondiente en el árbol sin cambiar su casilla de selección. Preferencias se organiza en las pestañas **Producción**, **Archivos** y **Cálculos**.

Las búsquedas en retazos admiten desde una tinta. Si no hay archivos marcados, **Sugerir combinaciones en retazos** utiliza todos los archivos RIP disponibles. El generador de subconjuntos se limita a la envolvente geométrica del inventario, recortada por los límites del tambor, no al tamaño nominal del tambor cuando este es mayor. Así, cambiar entre un límite 800×800 y 900×1200 no altera el conjunto candidato si todos los retazos caben en ambos. Retiene hasta 16 candidatos por cantidad y grupo de resolución para grupos de hasta 128 tintas, 9 para grupos de 129–256 y 3 para grupos mayores. La búsqueda muestra la mejor propuesta encontrada por retazo/resolución y no distribuye un mismo trabajo entre varios retazos.

**Sugerir combinaciones** evalúa cuatro perfiles geométricos por subconjunto y retiene hasta 16 candidatos por cantidad para grupos de resolución de hasta 128 tintas, 9 para grupos de 129–256 y 3 para grupos mayores. Es heurístico, no exhaustivo; el coste depende del número de tamaños distintos y subconjuntos retenidos.

El CSV debe estar en UTF-8 y tener los encabezados `ancho_mm`, `alto_mm` y `material`; `ubicacion` y `notas` son opcionales. El orden de columnas puede cambiar porque cada campo se identifica por su encabezado. Las medidas deben ser números finitos positivos en milímetros y el material debe estar dado de alta previamente. Se acepta coma o punto y coma como separador; para decimales con coma, usa punto y coma entre columnas. La importación valida todo el archivo antes de insertar: si una fila tiene texto en las medidas, material desconocido, campos sobrantes o incompletos, no importa ninguna y muestra las filas con error. Si se invierten los valores bajo los encabezados `ancho_mm` y `alto_mm`, el programa los interpretará según esos encabezados; no puede inferir que esos dos números debían intercambiarse.

Ejemplo:

```csv
ancho_mm;alto_mm;material;ubicacion;notas
320;230;Cartón;Estante A;Sin daño
230;320;Poliéster;Estante B;
```

Las pruebas se ejecutan con:

```powershell
python -m unittest discover -s tests -v
```

## Organizacion

- `cdi_optimizer/core/`: lectura de metadatos XMP, exploracion de trabajos y empaquetado geometrico.
- `cdi_optimizer/ui/`: ventana principal, preferencias, previsualizacion y estilos.
- `core.py` y `ui.py`: fachadas de compatibilidad para imports existentes.
- `tests/`: pruebas de extraccion y geometria.

## Alcance del acomodo

El motor usa MaxRects con varias ordenaciones, compactacion inferior-izquierda, busqueda de ancho de placa, rejillas explicitas para piezas identicas y giro individual opcional de 90 grados. Calcula perfiles internos de maximo aprovechamiento, equilibrio, ratio seguro y agrupacion dimensional; presenta solo geometrías unicas como **Opcion 1**, **Opcion 2**, etc. Si una pieza individual hace imposible cumplir el ratio configurado, se informa al operador para que pueda aumentarlo. La alternativa de retazo se muestra si su aprovechamiento queda como maximo 3 puntos porcentuales por debajo de la placa unica, y sus particiones intentan conservar juntos los tamaños repetidos. Las sugerencias son independientes de los perfiles visuales, ofrecen hasta tres composiciones por cantidad y no mezclan resoluciones DPI. El recomendador es heuristico; no evalua todos los subconjuntos posibles.

La separacion minima se aplica como margen alrededor de cada grabado: 1 mm agrega 2 mm al ancho y al alto de su contorno de empaquetado, y deja ese margen entre el arte y el borde de la placa. Al quedar margen en ambos grabados, la distancia entre sus artes es dos veces el valor configurado. El porcentaje de aprovechamiento divide el area original de los grabados entre la envolvente interior compactada sin esos margenes, excluyendo margenes, pinza y tolerancia fija. El espacio desperdiciado por la forma del conjunto si cuenta. La alternativa de retazo solo se muestra si su aprovechamiento interior sin redondear iguala o supera el de la placa unica.

La opcion doble representa dos rectangulos de alturas y anchos independientes, unidos por sus lados verticales y con sus bordes inferiores alineados. Cada retazo se mantiene dentro de los limites configurados del tambor; el conjunto unido puede superar el ancho del tambor. El motor modela las imagenes como rectangulos a partir de `egGr:vsize` y `egGr:hsize`: no interpreta contornos, sangrados especiales ni restricciones de orientacion propias del material.

Antes de usar resultados en maquina, valida las tolerancias fisicas, el montaje de retazos y las restricciones de orientacion del material con el operador.