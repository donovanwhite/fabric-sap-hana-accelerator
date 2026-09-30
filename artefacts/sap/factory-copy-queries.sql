/*
Fabric Factory full-snapshot Copy query templates.

The selected SAP projections do not expose a reliable change timestamp or
deletion feed. Each successful run therefore extracts the complete projection.
Silver performs business-key Delta MERGE upserts.
*/

SELECT
    RCLNT,RLDNR,RBUKRS,GJAHR,POPER,BELNR,DOCLN,RACCT,RCNTR,PRCTR,
    KOKRS,LIFNR,KUNNR,BUDAT,BLDAT,DRCRK,BLART,AWTYP,SGTXT,RHCUR,
    RKCUR,RWCUR,HSL,KSL,TSL,WSL,MSL
FROM SAPABAP1.ACDOCA;

/*
The pipeline repeats the same projection-only pattern for all 13 source
contracts and writes <table>_yyyyMMdd.csv. No source watermark is used.
*/
