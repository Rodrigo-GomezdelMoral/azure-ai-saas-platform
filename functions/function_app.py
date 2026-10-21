import azure.functions as func
from aggregate import bp

app = func.FunctionApp()
app.register_functions(bp)
