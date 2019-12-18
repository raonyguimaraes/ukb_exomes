from ukb_exomes import *
from ukb_common import *
from ukbb_qc import resources

temp_bucket = 'gs://ukb-pharma-exome-analysis-temp'


def main(args):
    hl.init(default_reference='GRCh38', log='/pheno_agg.log')
    ht = hl.read_matrix_table(get_ukb_exomes_mt_path()).cols()
    meta_ht = hl.read_table(get_ukb_exomes_meta_ht_path())
    print(meta_ht.filter(~meta_ht.is_filtered).count())
    meta_ht = meta_ht.filter(~meta_ht.is_filtered & (meta_ht.hybrid_pop == '12'))
    print(meta_ht.count())
    ht = ht.annotate(meta=meta_ht[ht.key])
    ht = ht.filter(hl.is_defined(ht.meta))

    mt = hl.read_matrix_table(get_ukb_pheno_mt_path(args.data_type, 'full'))
    mt = mt.filter_rows(hl.is_defined(ht[hl.str(mt.userId)]))

    if args.data_type == 'continuous':
        mt = mt.filter_cols(mt.coding != 'raw')

    extra_fields = compute_n_cases(mt, args.data_type)
    if args.data_type == 'icd':
        ht = mt.select_cols('truncated', 'meaning', **extra_fields).cols()
    else:
        ht = mt.select_cols('Abbvie_Priority', 'Biogen_Priority', 'Pfizer_Priority', **extra_fields).cols()
        ht = ht.annotate(
            # TODO: fix scoring if we do anything but >= 1
            score=2 * hl.int((ht.Abbvie_Priority == 'h') | (ht.Biogen_Priority == 'h') | (ht.Pfizer_Priority == 'h')) +
                  hl.int((ht.Abbvie_Priority == 'm') | (ht.Biogen_Priority == 'm') | (ht.Pfizer_Priority == 'm')))
    ht.export(get_phenotype_summary_tsv_path(args.data_type))



if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('--overwrite', help='Overwrite everything', action='store_true')
    parser.add_argument('--data_type', help='Data type', required=True, choices=('icd', 'continuous', 'categorical', 'biomarkers'))
    parser.add_argument('--slack_channel', help='Send message to Slack channel/user', default='@konradjk')
    args = parser.parse_args()

    if args.slack_channel:
        try_slack(args.slack_channel, main, args)
    else:
        main(args)