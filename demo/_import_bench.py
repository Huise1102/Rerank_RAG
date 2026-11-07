"""临时脚本：对比不同 import 写法的耗时（用完即删）。"""
import subprocess
import sys

CASES = [
    "from transformers import AutoModel, AutoTokenizer",
    "from transformers.models.bert.modeling_bert import BertModel",
    "from transformers.models.xlm_roberta.modeling_xlm_roberta import XLMRobertaForSequenceClassification",
    "from transformers.models.bert.tokenization_bert_fast import BertTokenizerFast",
    "from transformers.models.xlm_roberta.tokenization_xlm_roberta_fast import XLMRobertaTokenizerFast",
    "from transformers.models.auto.modeling_auto import MODEL_FOR_SEQUENCE_CLASSIFICATION_MAPPING_NAMES",
]

for code in CASES:
    snippet = (
        "import time; t=time.perf_counter();"
        f"{code}; print(f'{{time.perf_counter()-t:.2f}}s  {code[:58]}')"
    )
    subprocess.run([sys.executable, "-c", snippet], check=False)
