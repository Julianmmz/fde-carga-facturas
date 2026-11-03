---
titulo: Matriz de autorización de gastos
roles: analista, gerencia
---

# Matriz de autorización de gastos — AgroDemo S.A.

Documento ficticio. Versión 3, vigente desde el 01/07/2026. Responsable: Contabilidad.

## Alcance

Esta matriz define quién autoriza cada factura de proveedor antes de que se registre en el ERP.
Aplica a todas las facturas A, B y C recibidas por AgroDemo S.A., cualquiera sea el canal de
recepción (casilla de mail compartida o portal del proveedor). El importe que se compara contra
los umbrales es siempre el **importe total de la factura**, con IVA incluido.

## Umbrales de autorización

| Importe total de la factura | Quién autoriza | Cómo queda registrado |
|---|---|---|
| Hasta $ 500.000 inclusive | Analista contable que realiza la carga | Confirmación de la carga en el ERP |
| Más de $ 500.000 | Gerencia | Aprobación en el ERP con usuario y fecha |

Una factura de más de $ 500.000 queda bloqueada en el ERP hasta que Gerencia registra la
aprobación. El analista no puede finalizar la carga de una factura bloqueada.

## Facturas de servicios

Las facturas de servicios (reparaciones, mantenimiento, honorarios, fletes) se revisan completas
aunque no superen el umbral: el analista controla que el servicio se haya prestado consultando al
área que lo pidió. Son alrededor de 10 por mes.

## Rechazo de una factura

Si Gerencia rechaza una factura, el analista consulta al proveedor. Si el proveedor confirma un
error, envía una factura corregida y el circuito empieza de nuevo. Si no hay error, Gerencia vuelve
a evaluarla con la aclaración del proveedor.

## Registro y auditoría

Toda carga confirmada y toda aprobación quedan registradas con usuario, fecha y hora. El registro de
auditoría no se puede modificar. Una aprobación de Gerencia no se delega en el analista.
