
import sys
import openai
from langchain import LLMChain, PromptTemplate
from langchain.embeddings import OpenAIEmbeddings
from langchain.llms import OpenAI
from langchain.chat_models import ChatOpenAI
from langchain.vectorstores import Chroma, Pinecone
import numpy as np
import json
import os
import pandas as pd
import pinecone

#llm_model_name = "gpt-3.5-turbo"
llm_model_name = "text-davinci-003"

with open('prompts/hpo_extractor_template') as t:
    template = t.readlines()
    template = ''.join(template)


def make_hpo_dataframe():
    def gather_definition(meta_dict):
        if isinstance(meta_dict, dict):
            if 'definition' in meta_dict.keys():
                return meta_dict['definition']['val']
            else:
                return np.nan
        else:
            return np.nan

    def gather_comments(meta_dict):
        if isinstance(meta_dict, dict):
            if 'comments' in meta_dict.keys():
                return meta_dict['comments']
            else:
                return np.nan
        else:
            return np.nan

    def str_to_embed(df_row):
        string_template = ""
        if pd.notnull(df_row['lbl']):
            string_template += f"label: {df_row['lbl']} \n"
        if pd.notnull(df_row['definition']):
            string_template += f"definition: {df_row['definition']} \n"
        if pd.notnull(df_row['comments']):
            string_template += "comments: "
            for comment in df_row['comments']:
                string_template += f"{comment} \n"
        return string_template

    with open('hp.json') as f:
        data = json.load(f)

    hpo_data = data['graphs'][0]['nodes']

    hpo_data_df = pd.DataFrame(hpo_data)

    simple_hpo_df = pd.DataFrame(columns=['id', 'lbl', 'definition', 'comments'])

    simple_hpo_df['id'] = hpo_data_df['id']
    simple_hpo_df['type'] = hpo_data_df['id'].apply(lambda x: str(x)[-11:-8])
    simple_hpo_df['lbl'] = hpo_data_df['lbl']
    simple_hpo_df['definition'] = hpo_data_df['meta'].apply(gather_definition)
    simple_hpo_df['comments'] = hpo_data_df['meta'].apply(gather_comments)
    print(simple_hpo_df)
    simple_hpo_df = simple_hpo_df.loc[simple_hpo_df['type'] == '/HP'].dropna()
    print(simple_hpo_df)

    simple_hpo_df['id_int'] = simple_hpo_df['id'].apply(lambda x: int(str(x)[-7:]))

    simple_hpo_df['text_to_embed'] = simple_hpo_df.apply(str_to_embed, axis=1)

    return simple_hpo_df


def make_hpo_tree():
    class Tree:
        def __init__(self):
            self.tree = {}
            self.parents = {}

        def add_edge(self, sub, obj):
            if obj in self.tree:
                self.tree[obj].add(sub)
            else:
                self.tree[obj] = set([sub])

            self.parents[sub] = obj

        def get_children(self, id):
            if id in self.tree:
                return self.tree[id]
            else:
                return []

        def get_parent(self, id):
            if id in self.parents:
                return self.parents[id]
            else:
                return None

    def create_tree_from_list(list_of_dicts):
        tree = Tree()
        for dic in list_of_dicts:
            tree.add_edge(dic["sub"], dic["obj"])
        return tree

    with open('hp.json') as f:
        data = json.load(f)

    edges_list = data['graphs'][0]['edges']
    tree_of_ids = create_tree_from_list(edges_list)
    return tree_of_ids


def extract_terms(transcript, verbose=True):
    if llm_model_name == "text-davinci-003":
        llm = OpenAI(model_name=llm_model_name)
    else:
        llm = ChatOpenAI(model_name=llm_model_name)
    embedding = OpenAIEmbeddings(openai_api_key=os.environ['OPENAI_API_KEY'])
    pinecone.init(
        api_key=os.environ['PINECONE_API_KEY'],
        environment=os.environ['PINECONE_API_ENV']
    )
    index_name = "hpo-embeddings"
    index = pinecone.Index(index_name)

    vectorstore = Pinecone(index=index, embedding_function=embedding.embed_query, text_key='text')

    list_of_extracted_terms = []
    for line in transcript.split("\n"):
        print(line)
        if line.startswith("Parent:"):
            if verbose:
                print("--------------------\n\n", line, '\n\n')
            similar_terms = vectorstore.similarity_search(line, k=10)

            concat_doc = ""
            for doc in similar_terms:
                concat_doc += doc.page_content + "\n\n"

            if verbose:
                print(concat_doc)

            prompt = PromptTemplate(
                input_variables=["parents_text", "hpo_doc"],
                template=template,
            )

            chain = LLMChain(llm=llm, prompt=prompt)

            # Run the chain only specifying the input variable.
            llm_response = chain.run({"parents_text": line, "hpo_doc": concat_doc})
            for term in llm_response.split(', '):
                term = term.strip()
                if term.lower().rstrip('.') != 'none':
                    list_of_extracted_terms.append(term)
            if verbose:
                print(llm_response)
    if verbose:
        print("\n----------------------------\n\n")
    set_of_extracted_terms = set(list_of_extracted_terms)
    if verbose:
        print("Extracted terms : ", set(list_of_extracted_terms))
    return set_of_extracted_terms


if __name__ == "__main__":
    with open('sample generated transcripts/gpt3.5-sample1-angelman.json', 'r') as json_file:
        data = json.load(json_file)

    transcript = data['transcript']
    true_terms = set(data['true_terms'])

    hpo_tree = make_hpo_tree()
    hpo_df = make_hpo_dataframe()

    print(hpo_df['text_to_embed'].loc[hpo_df['id'] == 'http://purl.obolibrary.org/obo/HP_0000118'], '\n\n')
    child_list = list(hpo_tree.get_children('http://purl.obolibrary.org/obo/HP_0000118'))

    print(hpo_df['text_to_embed'].loc[child_list])

    sys.exit()

    extracted_terms = extract_terms(transcript=transcript, verbose=False)