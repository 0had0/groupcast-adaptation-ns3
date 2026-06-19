function pr = prr_cal(P, d, n, k)
% PRR_CAL  Calibrated link reception probability for MCS position k at distance
% d (m) behind n NLOSv blockers. Reduced-form of the SINR/BLER chain:
%   g(d,n) = PLexp*log10(d) + Cblock*n           (effective excess loss, dB)
%   PRR_k  = 1/2 erfc( (g - thr_k) / (sqrt2 s_k) ) -> logistic-like sigmoid
% thr_k is the per-MCS loss margin (= SNR0 - gamma_k), s_k its transition width.
% HARQ (<=Nmax tx, chase combining) is baked into the calibration to the ns-3
% PRR(d) curves. d,n may be arrays (same size); k scalar.
g = P.PLexp .* log10(max(d,1)) + P.Cblock .* n;
pr = 0.5 * erfc( (g - P.thr(k)) ./ (sqrt(2) * P.sk(k)) );
end
