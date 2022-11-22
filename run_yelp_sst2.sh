#bash run.sh task=yelp_polarity model=bert explainer=svs-200-sst2 seed=1 batch_size=1 device=0
bash run.sh task=yelp_polarity model=bert explainer=kernelshap-200-200-sst2 seed=1 batch_size=1 device=0
bash run.sh task=yelp_polarity model=bert explainer=kernelshap-2000-200-sst2 seed=1 batch_size=1 device=0
