## Check 1: next copy after a corrupt first copy (SINR50 = SINR where the binned success crosses 0.5)

```
   scenario      traffic       bg              pair  n_IR  next_sci_miss_IR  next_helper_IR  p_ok_IR  sinr50_IR  n_noIR  next_sci_miss_noIR  next_helper_noIR  p_ok_noIR  sinr50_noIR  shift_db
    highway aperiodic1tb moderate     R3 vs R3_noIR 21695             0.000             0.0    0.460       10.4   21727               0.000               0.0      0.315         14.2       3.8
    highway aperiodic1tb     none     R3 vs R3_noIR  8751             0.000             0.0    0.689        7.6    8874               0.000               0.0      0.566         10.0       2.4
    highway  periodic1tb moderate     R3 vs R3_noIR  7564             0.000             0.0    0.728        7.7    7300               0.000               0.0      0.660          9.9       2.2
    highway  periodic1tb     none     R3 vs R3_noIR  4601             0.000             0.0    0.801        7.7    4611               0.000               0.0      0.737          9.7       2.0
 roundabout aperiodic1tb moderate     R3 vs R3_noIR 11749             0.000             0.0    0.281       10.3   11483               0.000               0.0      0.185         15.3       5.0
 roundabout aperiodic1tb     none     R3 vs R3_noIR  8098             0.000             0.0    0.177        8.0    8165               0.000               0.0      0.102         11.8       3.8
 roundabout  periodic1tb moderate     R3 vs R3_noIR  8637             0.000             0.0    0.268        8.1    8344               0.000               0.0      0.159         10.8       2.7
 roundabout  periodic1tb     none     R3 vs R3_noIR  6818             0.000             0.0    0.281        7.7    6780               0.000               0.0      0.138         10.6       2.9
roundaboutE aperiodic1tb moderate     R3 vs R3_noIR 16217             0.000             0.0    0.329       10.6   16238               0.000               0.0      0.231         14.8       4.2
roundaboutE aperiodic1tb     none     R3 vs R3_noIR 10348             0.000             0.0    0.226        7.9   10191               0.000               0.0      0.126         11.8       3.9
roundaboutE  periodic1tb moderate     R3 vs R3_noIR 10258             0.000             0.0    0.326        8.1   10512               0.000               0.0      0.216         11.2       3.1
roundaboutE  periodic1tb     none     R3 vs R3_noIR  8279             0.000             0.0    0.304        7.5    8275               0.000               0.0      0.156         10.7       3.2
    highway aperiodic1tb moderate R4.2 vs R4.2_noIR  4787             0.041             1.0    0.696        9.3    5194               0.042               1.0      0.566         13.6       4.3
    highway aperiodic1tb     none R4.2 vs R4.2_noIR  6708             0.006             1.0    0.896        7.6    6982               0.006               1.0      0.856         10.1       2.5
    highway  periodic1tb moderate R4.2 vs R4.2_noIR  5077             0.013             1.0    0.879        7.1    5571               0.020               1.0      0.850          9.9       2.8
    highway  periodic1tb     none R4.2 vs R4.2_noIR  3925             0.005             1.0    0.954        7.2    3921               0.007               1.0      0.924          9.9       2.7
 roundabout aperiodic1tb moderate R4.2 vs R4.2_noIR  4401             0.110             1.0    0.486       12.2    4587               0.094               1.0      0.440         14.9       2.7
 roundabout aperiodic1tb     none R4.2 vs R4.2_noIR  5617             0.060             1.0    0.505       11.7    5647               0.050               1.0      0.495         11.8       0.1
 roundabout  periodic1tb moderate R4.2 vs R4.2_noIR  6751             0.064             1.0    0.629       10.3    7076               0.062               1.0      0.602         11.0       0.7
 roundabout  periodic1tb     none R4.2 vs R4.2_noIR  6015             0.062             1.0    0.664        9.7    6016               0.052               1.0      0.614         10.6       0.9
roundaboutE aperiodic1tb moderate R4.2 vs R4.2_noIR  6825             0.075             1.0    0.553       12.6    6813               0.081               1.0      0.500         14.4       1.8
roundaboutE aperiodic1tb     none R4.2 vs R4.2_noIR  8037             0.028             1.0    0.675       11.2    8006               0.032               1.0      0.656         11.9       0.7
roundaboutE  periodic1tb moderate R4.2 vs R4.2_noIR  8656             0.043             1.0    0.694        9.8    8886               0.041               1.0      0.681         10.9       1.1
roundaboutE  periodic1tb     none R4.2 vs R4.2_noIR  7464             0.029             1.0    0.767        9.3    7337               0.035               1.0      0.717         10.8       1.5
```

## Check 2: failed first attempts, SCI miss vs corrupt decode

```
   scenario      traffic       bg       arm  first_copies  failed  sci_miss  corrupt  sci_miss_share
    highway aperiodic1tb moderate        R3         40248   24754      3059    21695           0.124
    highway aperiodic1tb     none        R3         39728    9170       419     8751           0.046
    highway  periodic1tb moderate        R3         39760    8503       939     7564           0.110
    highway  periodic1tb     none        R3         39760    5025       424     4601           0.084
 roundabout aperiodic1tb moderate        R3         22760   15427      3678    11749           0.238
 roundabout aperiodic1tb     none        R3         20288   10058      1960     8098           0.195
 roundabout  periodic1tb moderate        R3         22792   11293      2656     8637           0.235
 roundabout  periodic1tb     none        R3         20384    8757      1939     6818           0.221
roundaboutE aperiodic1tb moderate        R3         30624   20712      4495    16217           0.217
roundaboutE aperiodic1tb     none        R3         28528   12549      2201    10348           0.175
roundaboutE  periodic1tb moderate        R3         30680   13019      2761    10258           0.212
roundaboutE  periodic1tb     none        R3         28536   10530      2251     8279           0.214
    highway aperiodic1tb moderate   R3_noIR         40256   24591      2864    21727           0.116
    highway aperiodic1tb     none   R3_noIR         39736    9292       418     8874           0.045
    highway  periodic1tb moderate   R3_noIR         39760    8236       936     7300           0.114
    highway  periodic1tb     none   R3_noIR         39760    5006       395     4611           0.079
 roundabout aperiodic1tb moderate   R3_noIR         22760   15163      3680    11483           0.243
 roundabout aperiodic1tb     none   R3_noIR         20288   10052      1887     8165           0.188
 roundabout  periodic1tb moderate   R3_noIR         22792   10935      2591     8344           0.237
 roundabout  periodic1tb     none   R3_noIR         20384    8749      1969     6780           0.225
roundaboutE aperiodic1tb moderate   R3_noIR         30624   20581      4343    16238           0.211
roundaboutE aperiodic1tb     none   R3_noIR         28528   12427      2236    10191           0.180
roundaboutE  periodic1tb moderate   R3_noIR         30680   13568      3056    10512           0.225
roundaboutE  periodic1tb     none   R3_noIR         28536   10440      2165     8275           0.207
    highway aperiodic1tb moderate      R4.2         39568   25184      3288    21896           0.131
    highway aperiodic1tb     none      R4.2         39784    9108       443     8665           0.049
    highway  periodic1tb moderate      R4.2         39760    7917       703     7214           0.089
    highway  periodic1tb     none      R4.2         39760    5019       431     4588           0.086
 roundabout aperiodic1tb moderate      R4.2         22848   15357      3867    11490           0.252
 roundabout aperiodic1tb     none      R4.2         20408   10131      1973     8158           0.195
 roundabout  periodic1tb moderate      R4.2         22792   11072      2844     8228           0.257
 roundabout  periodic1tb     none      R4.2         20384    8794      2195     6599           0.250
roundaboutE aperiodic1tb moderate      R4.2         30784   20751      4624    16127           0.223
roundaboutE aperiodic1tb     none      R4.2         28584   12591      2276    10315           0.181
roundaboutE  periodic1tb moderate      R4.2         30680   13237      2876    10361           0.217
roundaboutE  periodic1tb     none      R4.2         28536   10505      2481     8024           0.236
    highway aperiodic1tb moderate R4.2_noIR         39568   25160      3087    22073           0.123
    highway aperiodic1tb     none R4.2_noIR         39784    9303       438     8865           0.047
    highway  periodic1tb moderate R4.2_noIR         39760    8910       636     8274           0.071
    highway  periodic1tb     none R4.2_noIR         39760    4920       396     4524           0.080
 roundabout aperiodic1tb moderate R4.2_noIR         22848   15508      4004    11504           0.258
 roundabout aperiodic1tb     none R4.2_noIR         20408    9990      2083     7907           0.209
 roundabout  periodic1tb moderate R4.2_noIR         22792   10923      2609     8314           0.239
 roundabout  periodic1tb     none R4.2_noIR         20384    8729      2116     6613           0.242
roundaboutE aperiodic1tb moderate R4.2_noIR         30784   20675      4557    16118           0.220
roundaboutE aperiodic1tb     none R4.2_noIR         28584   12444      2260    10184           0.182
roundaboutE  periodic1tb moderate R4.2_noIR         30680   13121      2687    10434           0.205
roundaboutE  periodic1tb     none R4.2_noIR         28536   10492      2350     8142           0.224
```

## Check 4: PRR effect of combining = share of member-TBs with a corrupt first copy and a next copy x gain on that copy

```
   scenario      traffic       bg              pair  member_tbs  first_corrupt_share  with_next_copy_share  next_copy_gain  implied_dprr  observed_dprr
    highway aperiodic1tb moderate     R3 vs R3_noIR       40248                0.539                 0.539           0.145         0.078          0.068
    highway aperiodic1tb     none     R3 vs R3_noIR       39728                0.220                 0.220           0.123         0.027          0.015
    highway  periodic1tb moderate     R3 vs R3_noIR       39760                0.190                 0.190           0.068         0.013          0.002
    highway  periodic1tb     none     R3 vs R3_noIR       39760                0.116                 0.116           0.064         0.007          0.001
 roundabout aperiodic1tb moderate     R3 vs R3_noIR       22760                0.516                 0.516           0.096         0.050          0.033
 roundabout aperiodic1tb     none     R3 vs R3_noIR       20288                0.399                 0.399           0.075         0.030          0.021
 roundabout  periodic1tb moderate     R3 vs R3_noIR       22792                0.379                 0.379           0.109         0.041          0.031
 roundabout  periodic1tb     none     R3 vs R3_noIR       20384                0.334                 0.334           0.143         0.048          0.043
roundaboutE aperiodic1tb moderate     R3 vs R3_noIR       30624                0.530                 0.530           0.098         0.052          0.039
roundaboutE aperiodic1tb     none     R3 vs R3_noIR       28528                0.363                 0.363           0.100         0.036          0.026
roundaboutE  periodic1tb moderate     R3 vs R3_noIR       30680                0.334                 0.334           0.110         0.037          0.049
roundaboutE  periodic1tb     none     R3 vs R3_noIR       28536                0.290                 0.290           0.148         0.043          0.033
    highway aperiodic1tb moderate R4.2 vs R4.2_noIR       39568                0.553                 0.121           0.130         0.016          0.004
    highway aperiodic1tb     none R4.2 vs R4.2_noIR       39784                0.218                 0.169           0.040         0.007          0.001
    highway  periodic1tb moderate R4.2 vs R4.2_noIR       39760                0.181                 0.128           0.029         0.004          0.014
    highway  periodic1tb     none R4.2 vs R4.2_noIR       39760                0.115                 0.099           0.030         0.003         -0.002
 roundabout aperiodic1tb moderate R4.2 vs R4.2_noIR       22848                0.503                 0.193           0.046         0.009          0.002
 roundabout aperiodic1tb     none R4.2 vs R4.2_noIR       20408                0.400                 0.275           0.010         0.003         -0.008
 roundabout  periodic1tb moderate R4.2 vs R4.2_noIR       22792                0.361                 0.296           0.027         0.008         -0.011
 roundabout  periodic1tb     none R4.2 vs R4.2_noIR       20384                0.324                 0.295           0.050         0.015          0.006
roundaboutE aperiodic1tb moderate R4.2 vs R4.2_noIR       30784                0.524                 0.222           0.053         0.012          0.006
roundaboutE aperiodic1tb     none R4.2 vs R4.2_noIR       28584                0.361                 0.281           0.019         0.005          0.004
roundaboutE  periodic1tb moderate R4.2 vs R4.2_noIR       30680                0.338                 0.282           0.013         0.004         -0.007
roundaboutE  periodic1tb     none R4.2 vs R4.2_noIR       28536                0.281                 0.262           0.050         0.013          0.016
```

## Check 3: helper copies of TB k sent after the leader started a newer TB on the same HARQ id

```
      arm  runs  helper_copies  copies_after_newer_tb_same_harq  max_copy_lag_ms
       R4   120         102377                                0             15.5
     R4.1   120          45681                                0             15.5
     R4.2   120          69249                                0             15.5
       R5   120          48483                                0             15.5
       R6   120          62110                                0             15.5
R4.1_noIR   120          48544                                0             15.5
R4.2_noIR   120          71392                                0             15.5
```
