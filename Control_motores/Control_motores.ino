#include <Arduino.h>
#include <math.h>
#include <string.h>

const int motor1Pin = 6;
const int motor2Pin = 9;
const float voltajeArduino = 5.0;

int nivelMotor1 = 0;
int nivelMotor2 = 0;

char comando[24];
byte posicion = 0;
bool comandoDemasiadoLargo = false;

void informarNiveles() {
  Serial.print("M1:");
  Serial.println(nivelMotor1);

  Serial.print("M2:");
  Serial.println(nivelMotor2);
}

void aplicarNivel(char destino, int nivel) {
  // Se mantiene tu cálculo PWM.
  int pwm = round(nivel * 255.0 / voltajeArduino);

  if (destino == '1' || destino == 'B') {
    analogWrite(motor1Pin, pwm);
    nivelMotor1 = nivel;
  }

  if (destino == '2' || destino == 'B') {
    analogWrite(motor2Pin, pwm);
    nivelMotor2 = nivel;
  }

  informarNiveles();
}

void procesarComando(const char* texto) {
  int longitud = strlen(texto);
  char destino;
  char caracterNivel;

  // Comando para ambos: B:3
  if (
    longitud == 3 &&
    texto[0] == 'B' &&
    texto[1] == ':'
  ) {
    destino = 'B';
    caracterNivel = texto[2];
  }

  // Comando individual: M1:3 o M2:3
  else if (
    longitud == 4 &&
    texto[0] == 'M' &&
    (texto[1] == '1' || texto[1] == '2') &&
    texto[2] == ':'
  ) {
    destino = texto[1];
    caracterNivel = texto[3];
  }

  else {
    Serial.println("ERROR: usa M1:n, M2:n o B:n");
    return;
  }

  if (caracterNivel < '0' || caracterNivel > '4') {
    Serial.println("ERROR: nivel entre 0 y 4");
    return;
  }

  aplicarNivel(destino, caracterNivel - '0');
}

void setup() {
  pinMode(motor1Pin, OUTPUT);
  pinMode(motor2Pin, OUTPUT);

  analogWrite(motor1Pin, 0);
  analogWrite(motor2Pin, 0);

  Serial.begin(9600);

  Serial.println("CONTROL DE DOS MOTORES");
  Serial.println("M1:n = Motor 1");
  Serial.println("M2:n = Motor 2");
  Serial.println("B:n  = Ambos motores");
  Serial.println("Nivel permitido: 0 a 4");

  informarNiveles();
}

void loop() {
  // Recibe líneas sin bloquear con parseInt().
  while (Serial.available() > 0) {
    char recibido = Serial.read();

    if (recibido == '\r') {
      continue;
    }

    if (recibido == '\n') {
      if (comandoDemasiadoLargo) {
        Serial.println("ERROR: comando demasiado largo");
      }
      else if (posicion > 0) {
        comando[posicion] = '\0';
        procesarComando(comando);
      }

      posicion = 0;
      comandoDemasiadoLargo = false;
    }
    else if (!comandoDemasiadoLargo) {
      if (posicion < sizeof(comando) - 1) {
        comando[posicion++] = recibido;
      }
      else {
        comandoDemasiadoLargo = true;
      }
    }
  }
}