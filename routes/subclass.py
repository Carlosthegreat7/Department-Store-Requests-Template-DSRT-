from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import SubClass
from portal import loggedin_required

# Create the Blueprint for Subclass logic
subclass_bp = Blueprint('subclass', __name__)

@subclass_bp.route('/admin/add_subclass', methods=['GET', 'POST'])
@loggedin_required()
def add_subclass():
    """Route to add a new subclass entry to the MySQL database."""
    if request.method == 'POST':
        try:
            # Capture data using names matching the redesigned add_subclass.html
            new_sub = SubClass(
                product_group=request.form.get('product_group', '').strip().upper(),
                subclass_code=request.form.get('subclass_code', '').strip().zfill(3),
                subclass_name=request.form.get('subclass_name', '').strip().upper()
            )
            
            db.session.add(new_sub)
            db.session.commit()
            
            flash(f"Successfully added Subclass: {new_sub.subclass_name}", "success")
            # FIXED: Added _anchor to persist the Subclass tab state
            return redirect(url_for('admin_management', _anchor='subclass'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Database Error: {e}", "danger")
            
    return render_template('add_subclass.html')

@subclass_bp.route('/admin/edit_subclass/<group>/<code>', methods=['GET', 'POST'])
@loggedin_required()
def edit_subclass(group, code):
    """Route to update an existing subclass using composite keys."""
    # Find the record using both group and code context
    sub = SubClass.query.filter_by(product_group=group, subclass_code=code).first_or_404()
    
    if request.method == 'POST':
        try:
            # Update fields from the redesigned edit form
            sub.product_group = request.form.get('product_group', '').strip().upper()
            sub.subclass_code = request.form.get('subclass_code', '').strip().zfill(3)
            sub.subclass_name = request.form.get('subclass_name', '').strip().upper()
            
            db.session.commit()
            flash("Subclass updated successfully!", "success")
            # FIXED: Added _anchor to persist the Subclass tab state
            return redirect(url_for('admin_management', _anchor='subclass'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating subclass: {e}", "danger")
            
    # Passing variable as 'subclass' to match the redesigned edit_subclass.html
    return render_template('edit_subclass.html', subclass=sub)

@subclass_bp.route('/admin/delete_subclass/<group>/<code>', methods=['POST'])
@loggedin_required()
def delete_subclass(group, code):
    """Route to remove a subclass using group and code context."""
    sub = SubClass.query.filter_by(product_group=group, subclass_code=code).first()
    
    if sub:
        try:
            db.session.delete(sub)
            db.session.commit()
            flash(f"Subclass {code} deleted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Delete Error: {e}", "danger")
    else:
        flash("Record not found.", "warning")
        
    # FIXED: Added _anchor to persist the Subclass tab state
    return redirect(url_for('admin_management', _anchor='subclass'))