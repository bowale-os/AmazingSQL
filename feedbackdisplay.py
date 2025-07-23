import json
import ast

def format_schema(schema):
        if "CREATE TABLE" not in schema:
            return schema
        
        schema = schema.replace('(', '(\n   ')
        schema = schema.replace(", ", ",\n    ")
        schema = schema.replace(");", "\n);")

        return schema
    
    
def generate_table_headers(expected_output):
    if not expected_output:
        return [], []
    if isinstance(expected_output, str):
        try:
            expected_output = ast.literal_eval(expected_output)

        except Exception as e:
            print("Error parsing string:", e)
            return [], []

        # Now expected_output is a list of lists or tuples
        headers = [f"Column {i+1}" for i in range(len(expected_output[0]))]
        return headers, expected_output



def compare_user_query(expected_output, user_output):
    # 1. Normalize input
    expected = [list(map(str, row)) for row in expected_output]
    user = [list(map(str, row)) for row in user_output]

    # 2. Sort rows for fair comparison (if order doesn't matter)
    expected_sorted = sorted(expected)
    user_sorted = sorted(user)

    # 3. Check row count
    if len(expected_sorted) != len(user_sorted):
        return ('warning', f"Expected {len(expected_sorted)} rows but got {len(user_sorted)}.")

    # 4. Check each row
    for idx, (exp_row, user_row) in enumerate(zip(expected_sorted, user_sorted)):
        if exp_row != user_row:
            differences = [
                f"Expected '{e}' but got '{u}' at column {i+1}"
                for i, (e, u) in enumerate(zip(exp_row, user_row)) if e != u
            ]
            return ('warning', f"Row {idx+1} has incorrect values. " + "; ".join(differences))

    return ('success', 'You solved it!')


def humanize_query_error(error: str) -> str:
    error = str(error).lower()  # Normalize

    if "does not exist" in error:
        if "relation" in error:
            return "It looks like the table you're trying to query doesn't exist. Check your FROM clause."
        if "column" in error:
            return "You're referencing a column that doesn't exist. Check your SELECT or WHERE clause for typos."
    
    if "syntax error" in error:
        return "There seems to be a syntax error. Double-check commas, keywords, and overall structure."

    if "must appear in the group by clause" in error:
        return "You're selecting a column that isn't grouped. Try adding it to the GROUP BY clause or using an aggregate function."

    if "no such column" in error:
        return "You're referencing a column that doesn't exist. Check for typos."

    if "near" in error:
        return f"There’s an error near {error.split('near')[1].strip()}. You may have a typo or missing punctuation."

    if "division by zero" in error:
        return "You're dividing by zero somewhere — that's not allowed."

    if "permission denied" in error:
        return "You don't have the required permissions for this operation."

    # Fallback
    return "An error occurred, but I couldn't interpret it clearly. Check your SQL syntax and table names."


# print(humanize_query_error('near "FROM": syntax error'))

     

     
