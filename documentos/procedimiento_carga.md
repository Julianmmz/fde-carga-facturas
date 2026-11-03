---
titulo: Procedimiento de carga de facturas de proveedores
roles: analista, gerencia
---

# Procedimiento de carga de facturas de proveedores

Documento ficticio. Procedimiento PR-CONT-07, revisión 2026. Área: Contabilidad.

<!-- Nota de edición: revisar redacción antes de la auditoría anual. -->

## Recepción de la factura

Las facturas llegan a la casilla compartida de proveedores o se descargan del portal de cada
proveedor recurrente. El analista guarda el PDF original sin modificarlo y lo adjunta a la carga.
Una foto de la factura solo se acepta si se leen todos los datos y el código QR.

## Datos que se cargan en el ERP

De cada factura se cargan 12 datos: CUIT del emisor, razón social, tipo y código de comprobante,
punto de venta, número, período facturado, fecha de vencimiento para el pago, artículo del ERP,
importe neto gravado, alícuota de IVA, importe de IVA e importe total. El artículo del ERP no figura
en la factura: se toma de la tabla de equivalencias de proveedores.

## Controles antes de confirmar

Antes de confirmar la carga, el analista verifica:

1. Que el dígito verificador del CUIT del emisor sea correcto.
2. Que el importe neto más el IVA sea igual al importe total.
3. Que la alícuota de IVA sea una de las vigentes: 0 %, 10,5 %, 21 % o 27 %.
4. Que una factura C no discrimine IVA.

Si algún control falla, la factura no se carga y se consulta al proveedor.

## Control de facturas duplicadas

Una factura es duplicada cuando ya existe en el ERP otra con el mismo CUIT del emisor, el mismo tipo
de comprobante, el mismo punto de venta y el mismo número. El ERP rechaza la carga de una duplicada;
si el analista la detecta antes, la descarta y avisa al proveedor si la volvió a enviar por error.

## Plazo de carga

Toda factura se carga dentro de los 2 días hábiles de recibida. Las facturas que vencen en menos de
5 días se cargan en el día.

## Error detectado después de finalizar la carga

Finalizar la carga no tiene vuelta atrás. Si después se detecta un error, el analista **anula el
comprobante en el ERP antes de la fecha de pago** para que no salga el pago, y vuelve a cargar la
factura con los datos correctos. La anulación queda en el registro de auditoría con el motivo.
