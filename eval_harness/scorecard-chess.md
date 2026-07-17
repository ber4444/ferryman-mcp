# ferryman eval scorecard — chess-opening-coach

Generated: 2026-07-17 00:16:18
Cases: 120

## Rule-scorer results

| Case | Provider | Pass rate | Failed checks | Latency | Cost |
|---|---|---|---|---|---|
| short_tactics_rating_beginner_0081 | hf-llama | 50% | exactMove | 5675 ms | $0.0001 |
| short_tactics_rating_beginner_0014 | hf-llama | 50% | exactMove | 7557 ms | $0.0001 |
| short_tactics_rating_beginner_0003 | hf-llama | 50% | exactMove | 35325 ms | $0.0002 |
| short_tactics_rating_beginner_0094 | hf-llama | 50% | exactMove | 61220 ms | $0.0002 |
| short_tactics_rating_beginner_0035 | hf-llama | 50% | exactMove | 52532 ms | $0.0002 |
| short_tactics_rating_intermediate_0031 | hf-llama | 50% | exactMove | 12696 ms | $0.0003 |
| short_tactics_rating_intermediate_0028 | hf-llama | 50% | exactMove | 3994 ms | $0.0001 |
| short_tactics_rating_intermediate_0017 | hf-llama | 50% | exactMove | 28064 ms | $0.0001 |
| short_tactics_rating_intermediate_0094 | hf-llama | 50% | exactMove | 4606 ms | $0.0002 |
| short_tactics_rating_intermediate_0013 | hf-llama | 50% | exactMove | 4545 ms | $0.0002 |
| short_tactics_rating_advanced_0086 | hf-llama | 50% | exactMove | 4873 ms | $0.0001 |
| short_tactics_rating_advanced_0094 | hf-llama | 50% | exactMove | 8086 ms | $0.0002 |
| short_tactics_rating_advanced_0069 | hf-llama | 50% | exactMove | 3377 ms | $0.0001 |
| short_tactics_rating_advanced_0011 | hf-llama | 50% | exactMove | 6827 ms | $0.0002 |
| short_tactics_rating_advanced_0075 | hf-llama | 50% | exactMove | 5565 ms | $0.0002 |
| short_tactics_rating_expert_0054 | hf-llama | 50% | exactMove | 8090 ms | $0.0003 |
| short_tactics_rating_expert_0004 | hf-llama | 50% | exactMove | 6653 ms | $0.0002 |
| short_tactics_rating_expert_0003 | hf-llama | 50% | exactMove | 4813 ms | $0.0001 |
| short_tactics_rating_expert_0011 | hf-llama | 50% | exactMove | 6311 ms | $0.0002 |
| short_tactics_rating_expert_0027 | hf-llama | 50% | exactMove | 7408 ms | $0.0002 |
| position_judgement_advantage_0029 | hf-llama | 100% | — | 8297 ms | $0.0002 |
| position_judgement_advantage_0064 | hf-llama | 100% | — | 5761 ms | $0.0002 |
| position_judgement_advantage_0077 | hf-llama | 50% | evalBand | 8977 ms | $0.0003 |
| position_judgement_advantage_0003 | hf-llama | 50% | evalBand | 4509 ms | $0.0002 |
| position_judgement_disadvantage_0071 | hf-llama | 50% | evalBand | 5842 ms | $0.0002 |
| position_judgement_disadvantage_0025 | hf-llama | 50% | evalBand | 5727 ms | $0.0002 |
| position_judgement_disadvantage_0091 | hf-llama | 50% | evalBand | 3995 ms | $0.0001 |
| position_judgement_disadvantage_0083 | hf-llama | 100% | — | 6555 ms | $0.0002 |
| position_judgement_losing_0089 | hf-llama | 50% | evalBand | 4686 ms | $0.0002 |
| position_judgement_losing_0069 | hf-llama | 50% | evalBand | 5852 ms | $0.0002 |
| position_judgement_losing_0053 | hf-llama | 50% | evalBand | 4410 ms | $0.0002 |
| position_judgement_losing_0028 | hf-llama | 50% | evalBand | 4394 ms | $0.0001 |
| position_judgement_neutral_0057 | hf-llama | 100% | — | 6759 ms | $0.0002 |
| position_judgement_neutral_0075 | hf-llama | 50% | evalBand | 4920 ms | $0.0002 |
| position_judgement_neutral_0035 | hf-llama | 50% | evalBand | 5014 ms | $0.0002 |
| position_judgement_neutral_0000 | hf-llama | 50% | evalBand | 5943 ms | $0.0002 |
| position_judgement_winning_0097 | hf-llama | 50% | evalBand | 5118 ms | $0.0002 |
| position_judgement_winning_0020 | hf-llama | 50% | evalBand | 6862 ms | $0.0003 |
| position_judgement_winning_0089 | hf-llama | 50% | evalBand | 5319 ms | $0.0002 |
| position_judgement_winning_0054 | hf-llama | 50% | evalBand | 12801 ms | $0.0002 |
| short_tactics_rating_beginner_0081 | gemini | 50% | forbiddenPhrases | 3362 ms | $0.0004 |
| short_tactics_rating_beginner_0014 | gemini | 100% | — | 3837 ms | $0.0005 |
| short_tactics_rating_beginner_0003 | gemini | 50% | exactMove | 3234 ms | $0.0005 |
| short_tactics_rating_beginner_0094 | gemini | 50% | exactMove | 3402 ms | $0.0005 |
| short_tactics_rating_beginner_0035 | gemini | 100% | — | 3048 ms | $0.0005 |
| short_tactics_rating_intermediate_0031 | gemini | 50% | exactMove | 3341 ms | $0.0005 |
| short_tactics_rating_intermediate_0028 | gemini | 100% | — | 4393 ms | $0.0005 |
| short_tactics_rating_intermediate_0017 | gemini | 50% | forbiddenPhrases | 3593 ms | $0.0005 |
| short_tactics_rating_intermediate_0094 | gemini | 50% | exactMove | 3074 ms | $0.0005 |
| short_tactics_rating_intermediate_0013 | gemini | 100% | — | 3258 ms | $0.0004 |
| short_tactics_rating_advanced_0086 | gemini | 50% | exactMove | 2984 ms | $0.0005 |
| short_tactics_rating_advanced_0094 | gemini | 50% | exactMove | 3317 ms | $0.0005 |
| short_tactics_rating_advanced_0069 | gemini | 50% | exactMove | 2848 ms | $0.0005 |
| short_tactics_rating_advanced_0011 | gemini | 50% | exactMove | 2821 ms | $0.0005 |
| short_tactics_rating_advanced_0075 | gemini | 50% | exactMove | 3495 ms | $0.0006 |
| short_tactics_rating_expert_0054 | gemini | 50% | exactMove | 3316 ms | $0.0005 |
| short_tactics_rating_expert_0004 | gemini | 50% | exactMove | 3318 ms | $0.0007 |
| short_tactics_rating_expert_0003 | gemini | 100% | — | 2906 ms | $0.0005 |
| short_tactics_rating_expert_0011 | gemini | 50% | exactMove | 3088 ms | $0.0004 |
| short_tactics_rating_expert_0027 | gemini | 50% | exactMove | 4064 ms | $0.0005 |
| position_judgement_advantage_0029 | gemini | 50% | evalBand | 2514 ms | $0.0003 |
| position_judgement_advantage_0064 | gemini | 50% | evalBand | 2544 ms | $0.0004 |
| position_judgement_advantage_0077 | gemini | 50% | evalBand | 3274 ms | $0.0006 |
| position_judgement_advantage_0003 | gemini | 100% | — | 3388 ms | $0.0005 |
| position_judgement_disadvantage_0071 | gemini | 50% | evalBand | 2900 ms | $0.0004 |
| position_judgement_disadvantage_0025 | gemini | 50% | evalBand | 3234 ms | $0.0004 |
| position_judgement_disadvantage_0091 | gemini | 50% | evalBand | 2752 ms | $0.0004 |
| position_judgement_disadvantage_0083 | gemini | 50% | evalBand | 2913 ms | $0.0005 |
| position_judgement_losing_0089 | gemini | 50% | evalBand | 2526 ms | $0.0004 |
| position_judgement_losing_0069 | gemini | 50% | evalBand | 2762 ms | $0.0004 |
| position_judgement_losing_0053 | gemini | 50% | evalBand | 2974 ms | $0.0005 |
| position_judgement_losing_0028 | gemini | 50% | evalBand | 3269 ms | $0.0006 |
| position_judgement_neutral_0057 | gemini | 100% | — | 2475 ms | $0.0004 |
| position_judgement_neutral_0075 | gemini | 100% | — | 3669 ms | $0.0004 |
| position_judgement_neutral_0035 | gemini | 100% | — | 2552 ms | $0.0004 |
| position_judgement_neutral_0000 | gemini | 100% | — | 2771 ms | $0.0004 |
| position_judgement_winning_0097 | gemini | 50% | forbiddenPhrases | 2758 ms | $0.0003 |
| position_judgement_winning_0020 | gemini | 50% | evalBand | 3079 ms | $0.0005 |
| position_judgement_winning_0089 | gemini | 50% | evalBand | 2661 ms | $0.0004 |
| position_judgement_winning_0054 | gemini | 50% | evalBand | 3271 ms | $0.0005 |
| short_tactics_rating_beginner_0081 | zai-glm | 50% | exactMove | 56956 ms | $0.0002 |
| short_tactics_rating_beginner_0014 | zai-glm | 50% | exactMove | 66248 ms | $0.0002 |
| short_tactics_rating_beginner_0003 | zai-glm | 50% | exactMove | 35533 ms | $0.0002 |
| short_tactics_rating_beginner_0094 | zai-glm | 50% | exactMove | 46163 ms | $0.0002 |
| short_tactics_rating_beginner_0035 | zai-glm | 50% | exactMove | 42715 ms | $0.0002 |
| short_tactics_rating_intermediate_0031 | zai-glm | 50% | exactMove | 68689 ms | $0.0002 |
| short_tactics_rating_intermediate_0028 | zai-glm | 50% | exactMove | 107900 ms | $0.0002 |
| short_tactics_rating_intermediate_0017 | zai-glm | 50% | exactMove | 39246 ms | $0.0002 |
| short_tactics_rating_intermediate_0094 | zai-glm | 50% | exactMove | 48072 ms | $0.0002 |
| short_tactics_rating_intermediate_0013 | zai-glm | 50% | exactMove | 34382 ms | $0.0002 |
| short_tactics_rating_advanced_0086 | zai-glm | 50% | exactMove | 34006 ms | $0.0002 |
| short_tactics_rating_advanced_0094 | zai-glm | 50% | exactMove | 34327 ms | $0.0002 |
| short_tactics_rating_advanced_0069 | zai-glm | 50% | exactMove | 51695 ms | $0.0008 |
| short_tactics_rating_advanced_0011 | zai-glm | 50% | exactMove | 56170 ms | $0.0002 |
| short_tactics_rating_advanced_0075 | zai-glm | 50% | exactMove | 51755 ms | $0.0002 |
| short_tactics_rating_expert_0054 | zai-glm | 50% | exactMove | 59494 ms | $0.0002 |
| short_tactics_rating_expert_0004 | zai-glm | 50% | exactMove | 46731 ms | $0.0002 |
| short_tactics_rating_expert_0003 | zai-glm | 50% | exactMove | 45842 ms | $0.0002 |
| short_tactics_rating_expert_0011 | zai-glm | 50% | exactMove | 45050 ms | $0.0002 |
| short_tactics_rating_expert_0027 | zai-glm | 50% | exactMove | 88268 ms | $0.0002 |
| position_judgement_advantage_0029 | zai-glm | 50% | evalBand | 58885 ms | $0.0009 |
| position_judgement_advantage_0064 | zai-glm | 50% | evalBand | 43306 ms | $0.0009 |
| position_judgement_advantage_0077 | zai-glm | 50% | evalBand | 50070 ms | $0.0012 |
| position_judgement_advantage_0003 | zai-glm | 50% | evalBand | 80191 ms | $0.0012 |
| position_judgement_disadvantage_0071 | zai-glm | 50% | evalBand | 49558 ms | $0.0002 |
| position_judgement_disadvantage_0025 | zai-glm | 50% | evalBand | 55789 ms | $0.0002 |
| position_judgement_disadvantage_0091 | zai-glm | 50% | evalBand | 25112 ms | $0.0011 |
| position_judgement_disadvantage_0083 | zai-glm | 50% | evalBand | 52968 ms | $0.0002 |
| position_judgement_losing_0089 | zai-glm | 50% | evalBand | 54029 ms | $0.0002 |
| position_judgement_losing_0069 | zai-glm | 100% | — | 64623 ms | $0.0012 |
| position_judgement_losing_0053 | zai-glm | 50% | evalBand | 53866 ms | $0.0015 |
| position_judgement_losing_0028 | zai-glm | 50% | evalBand | 48135 ms | $0.0002 |
| position_judgement_neutral_0057 | zai-glm | 50% | evalBand | 58457 ms | $0.0002 |
| position_judgement_neutral_0075 | zai-glm | 50% | evalBand | 53834 ms | $0.0011 |
| position_judgement_neutral_0035 | zai-glm | 50% | evalBand | 21123 ms | $0.0012 |
| position_judgement_neutral_0000 | zai-glm | 50% | evalBand | 31325 ms | $0.0014 |
| position_judgement_winning_0097 | zai-glm | 50% | evalBand | 80191 ms | $0.0002 |
| position_judgement_winning_0020 | zai-glm | 50% | evalBand | 49865 ms | $0.0011 |
| position_judgement_winning_0089 | zai-glm | 50% | evalBand | 54177 ms | $0.0002 |
| position_judgement_winning_0054 | zai-glm | 50% | evalBand | 20568 ms | $0.0008 |

**Overall rule pass rate: 56%**

## Per-provider summary

| Provider | Cases | Mean pass rate | Mean latency | Mean cost | Pricing date |
|---|---|---|---|---|---|
| gemini | 40 | 62% | 3124 ms | $0.0005 | 2026-07-16 |
| hf-llama | 40 | 55% | 9998 ms | $0.0002 | 2026-07-15 |
| zai-glm | 40 | 51% | 51632 ms | $0.0005 | 2026-07-16 |
