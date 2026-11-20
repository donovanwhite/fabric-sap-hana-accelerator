/*
Fabric Factory business-selection examples for the synthetic SQL source.

The deployed queries are generated from factory-copy-contract.json by
sap_extraction.py, including source controls and safely validated predicates.
The examples omit controls for readability and do not create SAP objects.
CPUDT/CPUTM discover document entry, not every subsequent source modification.
*/

SELECT
    RCLNT,RLDNR,RBUKRS,GJAHR,POPER,BELNR,DOCLN,RACCT,RCNTR,PRCTR,
    KOKRS,LIFNR,KUNNR,BUDAT,BLDAT,DRCRK,BLART,AWTYP,SGTXT,RHCUR,
    RKCUR,RTCUR,RWCUR,HSL,KSL,TSL,WSL,MSL
FROM SAPABAP1.ACDOCA;

/*
The same business projection supports full snapshots. For document discovery,
the complete source key joins BKPF; fiscal year and posting date are NOT
modification watermarks. The literal window below is an illustrative inclusive
same-second boundary, not a hardcoded runtime schedule or customer schema.
*/

SELECT
    A.RCLNT,A.RLDNR,A.RBUKRS,A.GJAHR,A.POPER,A.BELNR,A.DOCLN,A.RACCT,
    A.RCNTR,A.PRCTR,A.KOKRS,A.LIFNR,A.KUNNR,A.BUDAT,A.BLDAT,A.DRCRK,
    A.BLART,A.AWTYP,A.SGTXT,A.RHCUR,A.RKCUR,A.RTCUR,A.RWCUR,
    A.HSL,A.KSL,A.TSL,A.WSL,A.MSL
FROM SAPABAP1.ACDOCA A
INNER JOIN SAPABAP1.BKPF B
    ON A.RCLNT=B.MANDT AND A.RBUKRS=B.BUKRS
   AND A.GJAHR=B.GJAHR AND A.BELNR=B.BELNR
WHERE A.RCLNT='100'
  AND (B.CPUDT>'20261115' OR (B.CPUDT='20261115' AND B.CPUTM>='090000'))
  AND (B.CPUDT<'20261116' OR (B.CPUDT='20261116' AND B.CPUTM<='090000'));

/* A complete logical partition refresh reads ACDOCA without the BKPF join,
   retaining headerless and older modified records inside that scope. */
SELECT
    RCLNT,RLDNR,RBUKRS,GJAHR,POPER,BELNR,DOCLN,RACCT,RCNTR,PRCTR,
    KOKRS,LIFNR,KUNNR,BUDAT,BLDAT,DRCRK,BLART,AWTYP,SGTXT,RHCUR,
    RKCUR,RTCUR,RWCUR,HSL,KSL,TSL,WSL,MSL
FROM SAPABAP1.ACDOCA
WHERE RCLNT='100' AND GJAHR='2025' AND RBUKRS='1000' AND RLDNR='0L';
