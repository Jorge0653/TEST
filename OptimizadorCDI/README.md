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
python -m PyInstaller --noconfirm --clean --windowed --onefile --name OptimizadorCDI-final-v13 --icon app.ico --add-data "logo.png;." --add-data "app.ico;." main.py
```

El archivo se genera en `dist/OptimizadorCDI-final-v13.exe`. El ejecutable se puede copiar a otra computadora Windows sin instalar Python; la carpeta RIP debe configurarse en Preferencias en cada equipo.

La carpeta de salida del RIP, las dimensiones maximas del tambor, la tolerancia de corte y el ratio maximo alto/ancho se configuran en **Preferencias**. El ratio maximo inicia en 3:1 y se puede ajustar entre 1:1 y 10:1. **Calcular acomodo** genera hasta cuatro opciones geometricas con miniaturas pequeñas en el panel de controles; al elegir una, se ve ampliada en el lienzo central. **Buscar mas variantes** prueba hasta cinco perfiles adicionales, de uno en uno, e incorpora como maximo dos opciones nuevas por intento, con un limite de diez opciones visibles. **Sugerir combinaciones** agrupa las variantes por cantidad de grabados, no muestra el ratio y prioriza grupos de grabados identicos cuando el aprovechamiento empata. La carpeta RIP se vigila para retirar automaticamente los `.len` borrados y limpiar su seleccion guardada. Los calculos se pueden cancelar; al cerrar durante un calculo, la aplicacion solicita su cancelacion y libera el observador de carpetas.

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