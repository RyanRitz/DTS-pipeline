/* =====================================================================
   DMR MAIDEN config-F scoring block  (validation oracle for the Python port)
   ---------------------------------------------------------------------
   Twin of the turf %dmrtscore block in Scoring_DMR_2026.sas. REPLACES the
   legacy SAR 32-cell maiden section (the maiden1..maiden16 + NY proc score
   chain that builds validated_madien). Produces validated_madien with the
   same key columns so the downstream `data validated;` combine is untouched.

   Blend = mean(cell, surface-parent, racetype-parent, distance-parent) of 4 of
   the 14 DMR_Maid_2026_* logistic models. No core (baseprob2 is a within-model
   forced term, not a blend component). Matches score_dmr_maiden.py exactly.

   Cell routing per horse:  surface(T/D) x racetype(S/M) x distance(sp/rt).
   Coeffs = SASDATA.DMR_Maid_2026_{surfT,surfD,rtS,rtM,distSp,distRt,
            TSsp,TSrt,TMsp,TMrt,DSsp,DSrt,DMsp,DMrt}  (outest from the build).

   Vars built below = the SELECTED vars that are NOT already on DRF...final
   (the turf block builds the same *_dmrt26 family on its own subset, so the
   maiden subset must rebuild them). Every OTHER selected var is assumed present
   on DRF...final from the standardize region / the turf shared-19 (jcky_d,
   EPS_SAR25, xHBL4c, XBPPR_tc12(_3), xBRISPd3, trn102021, histspd_dmrd,
   BrisRelated_dmrd, IAucPri_keeA25, ipurseSAR25, xBRIS_DsPRn_dc, numbulls3,
   xRaceDate1_25, xDRFSPR2SAR, xks_w_winpcta, wotimefrlg_keeom, jwps_sarm,
   xJkyWCMstd_sarm, IJKYatDisJkyonTurfEPS_keeom, xStretchBtnLngthsonly1_keeod,
   xLTrecWPpct_kta13, xWorkoutDate3_keeod, wotimefrlg_sart, xLastWOatTT,
   xHC_MdntoMdnClm_C, xCABred, ShowedLateSP_LR, DRF1_SART, iJCK_EPS2025,
   jcky_keeapraw13, lrclass_kma13, BestBris0422, xdrfsp1m_sard, trnwcm_sart,
   PPt12). If the first run's log flags "variable not found" on any of these,
   build it here (same iterate-on-log flow the turf validation used).
   Validation pre-check (DMR 09/04 final): xCABred and xHC_MdntoMdnClm_C are NOT
   on DRF...final -> now built below. mvars had histspd_dmrd (selected by no
   model) in place of ltstr_sart (selected in surfT) -> SAS was silently
   dropping ltstr_sart from surfT; mvars now equals the 57 selected exactly.
   ===================================================================== */

/* ---- maiden subset + the config-F vars the maiden models select ---- */
data dmr_maiden;
  set SASDATA.DRF_&trck&date&year.final;
  if racetype in ('M','S') and track='DMR';

  baseprob2 = 1/horsesran;

  /* ---------- SPEED / FIGURE ---------- */
  if IBestBRISSpeedLife=. then spdlf_sard26=1;
    else if IBestBRISSpeedLife<.92 then spdlf_sard26=.92;
    else if IBestBRISSpeedLife>1.08 then spdlf_sard26=1.08;
    else spdlf_sard26=IBestBRISSpeedLife;
  if xBestBRISSpdDist=. then BRISDist_dmr26alt=0;
    else if xBestBRISSpdDist>10 then BRISDist_dmr26alt=10;
    else if xBestBRISSpdDist<-10 then BRISDist_dmr26alt=-10;
    else BRISDist_dmr26alt=xBestBRISSpdDist;
  if xBestBRISSpdTurf = . then turfspd_dmrt26 = -1.3;
    else turfspd_dmrt26 = max(-10, min(10, xBestBRISSpdTurf));
  if xBRISSpeedRating1=. then lastbris_dmrd=0;
    else if xBRISSpeedRating1<-8 then lastbris_dmrd=-8;
    else if xBRISSpeedRating1>5 then lastbris_dmrd=5;
    else lastbris_dmrd=xBRISSpeedRating1;
  xBRISPd2a=(xBRISPda+12)**2;

  /* ---------- CLASS / EARNINGS ---------- */
  if IEarningsFASTDirt > 6 then dirtearn_dmrt26 = 6;
    else dirtearn_dmrt26 = IEarningsFASTDirt;

  /* ---------- PACE ---------- */
  temp4f = mean(of xBRISFourfPaceFig1-xBRISFourfPaceFig3);
  if temp4f=. then pace4f_dmrt26=0; else pace4f_dmrt26=max(-15,min(15,temp4f));
  if xBRISLatePaceFig1<=0 or xBRISLatePaceFig1=. then latepace_kma13=0;
    else latepace_kma13=1;

  /* ---------- FORM / TRIP ---------- */
  if xFinishBtnLngthsonly1=. then finbtn_dmrn=8;
    else if xFinishBtnLngthsonly1>8 then finbtn_dmrn=8;
    else if xFinishBtnLngthsonly1<-8 then finbtn_dmrn=-8;
    else finbtn_dmrn=xFinishBtnLngthsonly1;
  if xStretchBtnLngthsonly1 = . then strbtn_dmrt26 = 1.0;
    else if xStretchBtnLngthsonly1 >  7 then strbtn_dmrt26 =  7;
    else if xStretchBtnLngthsonly1 < -5 then strbtn_dmrt26 = -5;
    else strbtn_dmrt26 = xStretchBtnLngthsonly1;
  if xStartsFASTDirt=. then xstrtsFT_kta13=0;
    else if xStartsFASTDirt>6 then xstrtsFT_kta13=6;
    else if xStartsFASTDirt<-6 then xstrtsFT_kta13=-6;
    else xstrtsFT_kta13=xStartsFASTDirt;

  /* ---------- LAYOFF / FIELD ---------- */
  if xaveragedaysoff5=. then xdaysoff5=-25; else xdaysoff5=xaveragedaysoff5;
  if xdaysoff5>=40 then xdaysoff5=40; else if xdaysoff5<=-40 then xdaysoff5=-40;
  if xhorsenum=. then xhnumsar=0; else if xhorsenum>1 then xhnumsar=0; else xhnumsar=1;

  /* ---------- PEDIGREE / SALE ---------- */
  if xBRISAvePedRating=. then xBRISAvePedRatKEE1017=0; else xBRISAvePedRatKEE1017=xBRISAvePedRating;
  if xBRISAvePedRatKEE1017>10 then xBRISAvePedRatKEE1017=10;
    else if xBRISAvePedRatKEE1017<-10 then xBRISAvePedRatKEE1017=-10;
  if xAuctionPrice=. then auct_kma13=0;
    else if xAuctionPrice>100000 then auct_kma13=100000;
    else if xAuctionPrice<-10000 then auct_kma13=-10000;
    else auct_kma13=xAuctionPrice;

  /* ---------- TRAINER ---------- */
  if xtran_itm_58 = . then trnitmturf_dmrt26 = -7;
    else if xtran_itm_58 >  15 then trnitmturf_dmrt26 =  15;
    else if xtran_itm_58 < -15 then trnitmturf_dmrt26 = -15;
    else trnitmturf_dmrt26 = xtran_itm_58;
  if xtran_wpct_58 = . then xtran_wpct_58c = -5.2;
    else if xtran_wpct_58 >  13 then xtran_wpct_58c =  13;
    else if xtran_wpct_58 < -13 then xtran_wpct_58c = -13;
    else xtran_wpct_58c = xtran_wpct_58;
  if xtran_st_34=. then SMW_trn_strs=0;
    else if xtran_st_34>100 then SMW_trn_strs=100;
    else if xtran_st_34<-100 then SMW_trn_strs=-100;
    else SMW_trn_strs=xtran_st_34;

  /* ---------- WORKOUTS ---------- */
  if nmiss(of iworkouttime1Bullet,iworkouttime2Bullet,iworkouttime3Bullet)=3 then iworkoutbullets=.;
    else iworkoutbullets=sum(iworkouttime1Bullet,iworkouttime2Bullet,iworkouttime3Bullet);
  if iworkoutbullets=. then wobulls_keeot=1; else wobulls_keeot=iworkoutbullets;
  if wobulls_keeot>9 then wobulls_keeot=9; if wobulls_keeot<0 then wobulls_keeot=0;

  if iworkoutpctrnk1>2 then iworkoutpctrnk1_cdmrm=2; else if iworkoutpctrnk1<.15 then iworkoutpctrnk1_cdmrm=.15; else iworkoutpctrnk1_cdmrm=iworkoutpctrnk1;
  if iworkoutpctrnk2>2 then iworkoutpctrnk2_cdmrm=2; else if iworkoutpctrnk2<.15 then iworkoutpctrnk2_cdmrm=.15; else iworkoutpctrnk2_cdmrm=iworkoutpctrnk2;
  if iworkoutpctrnk3>2 then iworkoutpctrnk3_cdmrm=2; else if iworkoutpctrnk3<.15 then iworkoutpctrnk3_cdmrm=.15; else iworkoutpctrnk3_cdmrm=iworkoutpctrnk3;
  iworkout_dmrm=iworkoutpctrnk1_cdmrm+iworkoutpctrnk2_cdmrm+iworkoutpctrnk3_cdmrm;

  if iworkoutpctrnk1=. then iworkoutpctrnk1_ckta13=1.; else if iworkoutpctrnk1>2 then iworkoutpctrnk1_ckta13=2; else if iworkoutpctrnk1<.15 then iworkoutpctrnk1_ckta13=.15; else iworkoutpctrnk1_ckta13=iworkoutpctrnk1;
  if iworkoutpctrnk2=. then iworkoutpctrnk2_ckta13=1.; else if iworkoutpctrnk2>2 then iworkoutpctrnk2_ckta13=2; else if iworkoutpctrnk2<.15 then iworkoutpctrnk2_ckta13=.15; else iworkoutpctrnk2_ckta13=iworkoutpctrnk2;
  if iworkoutpctrnk3=. then iworkoutpctrnk3_ckta13=1.; else if iworkoutpctrnk3>2 then iworkoutpctrnk3_ckta13=2; else if iworkoutpctrnk3<.15 then iworkoutpctrnk3_ckta13=.15; else iworkoutpctrnk3_ckta13=iworkoutpctrnk3;
  iworkout_kta13=iworkoutpctrnk1_ckta13+iworkoutpctrnk2_ckta13+iworkoutpctrnk3_ckta13;

  /* ---------- NOT on DRF...final (validation pre-check) ---------- */
  /* xHC_MdntoMdnClm_C  (BTSM_DMR_MaidenModel_2026.sas 2019-2021) */
  if xHC_MdntoMdnClm NE . then xHC_MdntoMdnClm_C=xHC_MdntoMdnClm; else xHC_MdntoMdnClm_C=0;
  if xHC_MdntoMdnClm_C>.6 then xHC_MdntoMdnClm_C=.6;
  if xHC_MdntoMdnClm_C<-.6 then xHC_MdntoMdnClm_C=-.6;
  /* CABred base for xCABred (race-centered below; build 62/215/258) */
  if strip(StateCountryabrvw)='CA' then CABred=1; else CABred=0;
run;

/* ---- race-center CABred -> xCABred (build's proc sql; dirt block 2297-2309) ---- */
proc sql;
  create table _dmrmagg as select track,date,race, avg(CABred) as CABred_avg
  from dmr_maiden group by track,date,race;
quit;
proc sort data=dmr_maiden; by track date race; run;
proc sort data=_dmrmagg;   by track date race; run;
data dmr_maiden;
  merge dmr_maiden _dmrmagg; by track date race;
  xCABred = CABred - CABred_avg;
  if xCABred=. then xCABred=0;
run;

/* ---- 57-var selected union (newvars trick picks per-model non-missing coeffs) ---- */
%let mvars =
  baseprob2 auct_kma13 drf1_sart EPS_SAR25 ltstr_sart iJCK_EPS2025 iworkout_kta13
  lrclass_kma13 ShowedLateSP_LR SMW_trn_strs spdlf_sard26 trn102021 wotimefrlg_keeom
  XBPPR_tc12 xBRISAvePedRatKEE1017 xCABred xHBL4c xHC_MdntoMdnClm_C xhnumsar xLastWOatTT
  xStretchBtnLngthsonly1_keeod wobulls_keeot xtran_wpct_58c IAucPri_keeA25 ipurseSAR25
  xBRIS_DsPRn_dc pace4f_dmrt26 iworkout_dmrm lastbris_dmrd xBRISPd2a dirtearn_dmrt26
  IJKYatDisJkyonTurfEPS_keeom PPt12 xstrtsFT_kta13 BrisRelated_dmrd jcky_keeapraw13
  latepace_kma13 turfspd_dmrt26 xdaysoff5 BestBris0422 finbtn_dmrn xWorkoutDate3_keeod
  strbtn_dmrt26 XBPPR_tc12_3 xJkyWCMstd_sarm jwps_sarm numbulls3 trnitmturf_dmrt26
  xdrfsp1m_sard wotimefrlg_sart xLTrecWPpct_kta13 BRISDist_dmr26alt xDRFSPR2SAR
  trnwcm_sart xBRISPd3 xks_w_winpcta xRaceDate1_25;

/* ---- score one model: subset by cell/parent condition, apply its betas ---- */
%macro dmrmscore(nm, tbl, cond);
  data seg_&nm; set dmr_maiden; if 1 &cond; run;
  data _null_; length newvars $ 2000; set SASDATA.DMR_Maid_2026_&tbl;
    array v{*} &mvars; do i=1 to dim(v); if v[i] ne . then newvars=catx(" ",newvars,vname(v[i])); end;
    call symputx("newvars", newvars);
  run;
  proc score data=seg_&nm out=sc_&nm score=SASDATA.DMR_Maid_2026_&tbl type=parms; var &newvars; run;
  data sc_&nm(keep=track date race horsename rm_&nm);
    set sc_&nm catcherr; rm_&nm=res_marker;
  run;
  proc sort data=sc_&nm; by track date race horsename; run;
%mend dmrmscore;

/* parents */
%dmrmscore(surfT,  surfT,  %str(and surface in ('T','t')))
%dmrmscore(surfD,  surfD,  %str(and surface in ('D','d')))
%dmrmscore(rtS,    rtS,    %str(and racetype='S'))
%dmrmscore(rtM,    rtM,    %str(and racetype='M'))
%dmrmscore(distSp, distSp, %str(and sprint=1))
%dmrmscore(distRt, distRt, %str(and sprint=0))
/* cells */
%dmrmscore(TSsp, TSsp, %str(and surface in ('T','t') and racetype='S' and sprint=1))
%dmrmscore(TSrt, TSrt, %str(and surface in ('T','t') and racetype='S' and sprint=0))
%dmrmscore(TMsp, TMsp, %str(and surface in ('T','t') and racetype='M' and sprint=1))
%dmrmscore(TMrt, TMrt, %str(and surface in ('T','t') and racetype='M' and sprint=0))
%dmrmscore(DSsp, DSsp, %str(and surface in ('D','d') and racetype='S' and sprint=1))
%dmrmscore(DSrt, DSrt, %str(and surface in ('D','d') and racetype='S' and sprint=0))
%dmrmscore(DMsp, DMsp, %str(and surface in ('D','d') and racetype='M' and sprint=1))
%dmrmscore(DMrt, DMrt, %str(and surface in ('D','d') and racetype='M' and sprint=0))

/* ---- config-F blend: predicted = mean(cell, surf-parent, rt-parent, dist-parent) ---- */
proc sort data=dmr_maiden; by track date race horsename; run;
data validated_madien;
  merge dmr_maiden(keep=track date race horsename racetype sprint surface numofentries horsesran
                        ProgramNumberifavailable horsenum NYBredRace)
        sc_surfT sc_surfD sc_rtS sc_rtM sc_distSp sc_distRt
        sc_TSsp sc_TSrt sc_TMsp sc_TMrt sc_DSsp sc_DSrt sc_DMsp sc_DMrt;
  by track date race horsename;
  if date=mdy(03,03,1999) then delete;

  psurfT =1/(1+exp(-rm_surfT));  psurfD =1/(1+exp(-rm_surfD));
  prtS   =1/(1+exp(-rm_rtS));    prtM   =1/(1+exp(-rm_rtM));
  pdistSp=1/(1+exp(-rm_distSp)); pdistRt=1/(1+exp(-rm_distRt));
  pTSsp=1/(1+exp(-rm_TSsp)); pTSrt=1/(1+exp(-rm_TSrt));
  pTMsp=1/(1+exp(-rm_TMsp)); pTMrt=1/(1+exp(-rm_TMrt));
  pDSsp=1/(1+exp(-rm_DSsp)); pDSrt=1/(1+exp(-rm_DSrt));
  pDMsp=1/(1+exp(-rm_DMsp)); pDMrt=1/(1+exp(-rm_DMrt));

  if surface in ('T','t') then surf_marg=psurfT; else surf_marg=psurfD;
  if racetype='S'         then rt_marg  =prtS;   else rt_marg  =prtM;
  if sprint=1             then dist_marg=pdistSp; else dist_marg=pdistRt;
  if      surface in ('T','t') and racetype='S' and sprint=1 then cell=pTSsp;
  else if surface in ('T','t') and racetype='S' and sprint=0 then cell=pTSrt;
  else if surface in ('T','t') and racetype='M' and sprint=1 then cell=pTMsp;
  else if surface in ('T','t') and racetype='M' and sprint=0 then cell=pTMrt;
  else if surface in ('D','d') and racetype='S' and sprint=1 then cell=pDSsp;
  else if surface in ('D','d') and racetype='S' and sprint=0 then cell=pDSrt;
  else if surface in ('D','d') and racetype='M' and sprint=1 then cell=pDMsp;
  else                                                             cell=pDMrt;

  predicted = mean(of cell surf_marg rt_marg dist_marg);
run;

proc print data=validated_madien(obs=40);
  var race horsename racetype sprint surface predicted cell surf_marg rt_marg dist_marg;
run;

/* Dedicated maiden export for SAS-vs-Python validation (carries the components
   so the diff can be done at cell/parent level, not just the blend). */
proc export data=validated_madien
  (keep=track date race horsename racetype sprint surface predicted
        cell surf_marg rt_marg dist_marg)
  outfile="C:\Users\ryanr\Documents\BTSM\DMR\SAS_DATA\dmr_maiden_scored_&trck&date&year..csv"
  dbms=csv replace;
run;
