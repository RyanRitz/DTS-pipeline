/* =====================================================================
   validate_dmr_maiden.sas  --  headless SAS oracle for the DMR maiden
   config-F Python port (score_dmr_maiden.py).

   Reproduces ScoreIt_FINAL.sas's setup (trck/date/year, coredir, SASDATA
   libname) and then runs ONLY dmr_maiden_score_block.sas against the
   already-built SASDATA.DRF_&trck&date&year.final.

   Why no %ryan / %scoring here: %ryan only builds WORK.ALL; DRF...final is
   built inside %scoring (Scoring_DMR_2026.sas, last written ~line 671) and
   %scoring also re-scores and re-exports the whole card. The final dataset
   for this card already exists from the production run, so re-running
   %scoring would only overwrite production outputs. Neither
   Scoring_DMR_2026.sas nor ScoreIt_FINAL.sas is touched.

   Run headless:
     "C:\Program Files\SASHome\SASFoundation\9.4\sas.exe" -sysin validate_dmr_maiden.sas
        -log validate_dmr_maiden_DMR0904.log -nosplash
   Writes: DMR\SAS_DATA\dmr_maiden_scored_&trck&date&year..csv
   ===================================================================== */

%let trck=DMR;
%let date=0904;
%let year=2026;
%let mmdd=&date;
%let coredir=Users\ryanr\Documents\BTSM;

options fullstimer nofmterr;
libname SASDATA "c:\&coredir\&trck\SAS_DATA";

/* empty-segment guard used by the score macros (same as Scoring_DMR_2026.sas) */
data catcherr (keep=track date race horsename);
  set SASDATA.DRF_&trck&date&year.final;
  if _n_=1; date=mdy(03,03,1999); horsename='dummy'; race=22;
run;

%include "c:\&coredir\&trck\PROGRAMS\dmr_maiden_score_block.sas";
