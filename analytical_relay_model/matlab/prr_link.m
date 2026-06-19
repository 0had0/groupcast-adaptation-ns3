function pr = prr_link(P, sinrdB, k, Nmax, useFeedbackCap)
% PRR_LINK  Single-link packet reception probability for MCS position k under
% HARQ with chase combining. Each (re)transmission adds cc_gain_dB of
% effective SINR; success = at least one of the up-to-N copies decodes:
%   PRR = 1 - prod_{c=1..N} BLER( SINR + (c-1) cc_gain , k )
% N is capped by Nmax and, when useFeedbackCap, by how many HARQ rounds fit in
% the PDB (feedback-driven retx need a PSFCH round-trip each).
if nargin < 5, useFeedbackCap = true; end
N = Nmax;
if useFeedbackCap
    rounds = floor(P.PDB / (P.harq_rtt_slots * P.Tslot)) + 1;
    N = min(N, max(rounds,1));
end
pfail = ones(size(sinrdB));
for c = 1:N
    eff = sinrdB + (c-1) * P.cc_gain_dB;
    pfail = pfail .* bler_mcs(P, eff, k);
end
pr = 1 - pfail;
end
