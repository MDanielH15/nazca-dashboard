# NAZCA Racing — Dashboard de control de motores

Dashboard de escritorio desarrollado con Python, CustomTkinter y pyserial
para controlar dos motores DC mediante Arduino.

Permite seleccionar **Motor 1**, **Motor 2** o **Ambos motores**, ajustar
niveles de 0 a 4 y visualizar las respuestas del Arduino.

## Vista previa

![Dashboard NAZCA Racing con selector de motores y consola serial](vista_previa.png)

## Funcionalidades

- Interfaz oscura con colores verde profundo y dorado.
- Detección automática de puertos seriales.
- Conexión a 9600 baudios.
- Selector para Motor 1, Motor 2 y Ambos motores.
- Slider y botones de niveles 0–4.
- Conservación del nivel independiente de cada motor.
- Parada global de ambos motores.
- Indicador visual del nivel solicitado.
- Consola de mensajes enviados y recibidos.
- Manejo de errores de comunicación serial.

## Archivos del proyecto

| Archivo | Descripción |
| --- | --- |
| `nazca_dashboard.py` | Aplicación de escritorio |
| `ControlMotores/ControlMotores.ino` | Programa del Arduino |
| `vista_previa.png` | Captura del dashboard |
| `README.md` | Documentación |

## Requisitos

- Python 3.10 o posterior con Tkinter.
- Arduino IDE.
- Arduino conectado por USB.
- Circuito con driver y control PWM independiente para cada motor.
- Librerías Python: CustomTkinter y pyserial.

## Instalación

Desde la terminal de VSCode:

```bash
python -m pip install customtkinter==5.2.2 pyserial==3.5
```

Los demás módulos Python usados vienen incluidos en su instalación estándar.
Tkinter normalmente está incluido en el instalador de Python para Windows.

El programa Arduino no requiere instalar librerías adicionales.

## Configuración del Arduino

1. Abre `ControlMotores.ino` en Arduino IDE.
2. Selecciona la placa y el puerto correspondientes.
3. Carga el programa en el Arduino.
4. Cierra el Monitor Serie y el Serial Plotter antes de conectar el dashboard.

El programa utiliza:

| Motor | Pin PWM |
| --- | --- |
| Motor 1 | 6 |
| Motor 2 | 9 |

Es necesario utilizar el sketch que reconoce los comandos `M1:n`, `M2:n`
y `B:n`. El sketch anterior que recibía solamente un número controlaba
ambos motores juntos.

## Ejecutar el dashboard

Abre una terminal en la carpeta donde guardaste `nazca_dashboard.py`:

```bash
python nazca_dashboard.py
```

Después:

1. Selecciona el puerto del Arduino.
2. Pulsa **Conectar**.
3. Espera el inicio de la comunicación.
4. Selecciona Motor 1, Motor 2 o Ambos motores.
5. Ajusta el nivel con el slider o los botones.

Al conectar, el dashboard solicita nivel 0 para ambos motores.

## Controles

| Control | Comportamiento |
| --- | --- |
| Motor 1 | El nivel elegido afecta solamente al Motor 1 |
| Motor 2 | El nivel elegido afecta solamente al Motor 2 |
| Ambos motores | El nivel elegido se aplica a los dos |
| Botón 0 / PARAR | Solicita detener el destino seleccionado |
| E-STOP | Solicita detener ambos motores |
| Escape o espacio | Solicita detener ambos motores |
| Desconectar | Intenta enviar una parada global antes de cerrar |

Cambiar de selector no envía comandos ni modifica velocidades.

Cuando los motores tienen niveles diferentes y se selecciona Ambos motores,
el indicador muestra **MIXTO**. Elegir un nivel iguala ambas consignas.

## Protocolo serial

Configuración: **9600 baudios, 8 bits de datos, sin paridad y 1 bit de parada**.

Cada comando se transmite como texto ASCII y termina con un salto de línea.

| Comando | Acción |
| --- | --- |
| `M1:3` | Motor 1 pasa a nivel 3 |
| `M2:1` | Motor 2 pasa a nivel 1 |
| `B:2` | Ambos motores pasan a nivel 2 |
| `M1:0` | Motor 1 se apaga |
| `B:0` | Ambos motores se apagan |

El Arduino informa los niveles mediante mensajes como:

```text
M1:3
M2:1
```

Las filas **SOLICITADO / RX** muestran la consigna elegida y el último nivel
informado por el Arduino para cada motor.

## Niveles y PWM

Se mantiene la fórmula del programa original:

```cpp
int pwm = round(nivel * 255.0 / voltajeArduino);
```

Con `voltajeArduino = 5.0`:

| Nivel | Valor PWM | Ciclo de trabajo |
| --- | --- | --- |
| 0 | 0 | 0% |
| 1 | 51 | 20% |
| 2 | 102 | 40% |
| 3 | 153 | 60% |
| 4 | 204 | 80% |

El porcentaje del dashboard representa la escala de niveles 0–4.
Su 100% visual corresponde al nivel 4, no a PWM 255.

El dashboard no mide RPM, corriente ni tensión real del motor.

## Prueba inicial

1. Selecciona Motor 1 y solicita nivel 1.
2. Selecciona Motor 2 y solicita nivel 3.
3. Comprueba que cada motor conserva su nivel.
4. Selecciona Ambos motores: debe aparecer MIXTO.
5. Solicita nivel 2: ambos deben quedar en nivel 2.
6. Pulsa E-STOP: ambos deben recibir nivel 0.

La parada es por software y depende de que el enlace serial funcione.

## Problemas frecuentes

### No se encuentra CustomTkinter o serial

Ejecuta:

```bash
python -m pip install customtkinter==5.2.2 pyserial==3.5
```

Si el error continúa, selecciona en VSCode el intérprete Python donde
instalaste las dependencias.

### No aparecen puertos

Comprueba el cable USB, la conexión del Arduino y sus controladores.
Pulsa Actualizar.

### No se puede abrir el puerto

Cierra el Monitor Serie, el Serial Plotter y otras aplicaciones que
estén utilizando el mismo puerto.

### Se conecta, pero los motores no responden correctamente

Comprueba que cargaste el sketch que interpreta `M1:n`, `M2:n` y `B:n`,
que ambos programas usan 9600 baudios y que el circuito corresponde
a los pines configurados.
